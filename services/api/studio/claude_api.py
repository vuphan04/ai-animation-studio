"""Claude through the Anthropic API, billed to the operator's own API key.

The key sits in Windows Credential Manager and is read here right before a request; it never reaches the interface,
SQLite or a log. Each job is one streamed request whose answer must match a JSON schema. The SDK never retries by
itself: a resent paid request could be charged twice, so an unclear outcome goes to the cost ledger for the operator.
With fallbacks on, Anthropic reruns a request its safety classifiers decline on another model inside the same call,
and the reservation covers that second run too.
"""

from __future__ import annotations

import math
import time

import anthropic
import httpx2

from . import logs
from .breakdown_provider import DRAFT_SCHEMA, INPUT_KEYS, INSTRUCTIONS as BREAKDOWN_INSTRUCTIONS, BreakdownResult
from .domain import DomainError, canonical
from .openai_client import PlannerAnswered, PlannerNotSent, PlannerRemoteError
from .secrets import get_provider_key
from .series_extractor import EXTRACTION_SCHEMA, INSTRUCTIONS as EXTRACTION_INSTRUCTIONS, ExtractResult


log = logs.get("claude_api")
SECRET = "ANTHROPIC"
DEFAULT_MODEL = "claude-opus-5-5"
EFFORTS = {"low": "Thấp · nhanh và rẻ hơn", "medium": "Vừa · đề xuất", "high": "Cao · kỹ hơn, tốn token hơn"}
OUTPUT_LIMITS = (1024, 64000)
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# USD per million input and output tokens on Anthropic's price list, checked 2026-10-06.
RATES = {"claude-fable-5-1": (10.0, 50.0), "claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.1, 0.5),
         # Models a fallback may answer with, so the ledger can price that run.
         "claude-opus-5": (5.0, 25.0), "claude-opus-4-8": (5.0, 25.0), "claude-sonnet-5": (2.0, 10.0)}
# Claude Haiku 5.5 costs more once a prompt passes 100K tokens.
LONG_PROMPT = {"claude-haiku-5-5": (100_000, (0.5, 2.5))}
# `fallback` is the dearest per-token rate of the models Anthropic documents as that model's fallback; None means the
# model has no server-side fallback.
MODELS = {
    "claude-opus-5-5": {"label": "Claude Opus 5.5 · đề xuất", "fallback": (5.0, 25.0)},
    "claude-sonnet-5-5": {"label": "Claude Sonnet 5.5 · cân bằng", "fallback": (2.0, 10.0)},
    "claude-haiku-5-5": {"label": "Claude Haiku 5.5 · tiết kiệm", "fallback": None},
    "claude-fable-5-1": {"label": "Claude Fable 5.1 · mạnh nhất, giá cao", "fallback": (5.0, 25.0)},
}
NOTE = "Claude API tính phí theo token vào tài khoản Anthropic của khóa đã lưu. Khoản giữ là mức tối đa; khi bật model dự phòng, nó gồm cả một lần chạy lại."
STATUS_HINTS = {
    401: "Anthropic không nhận khóa API; nhập lại khóa trong Cài đặt → Dịch vụ AI.",
    403: "Khóa API không có quyền dùng model này.",
    404: "Tài khoản Anthropic của khóa này không dùng được model đã chọn.",
    429: "Anthropic đang giới hạn tốc độ hoặc hạn mức của tài khoản; thử lại sau.",
    529: "Anthropic đang quá tải; thử lại sau.",
}


def public_models() -> list[dict]:
    return [{"id": model, "label": info["label"], "input_rate": RATES[model][0], "output_rate": RATES[model][1],
             "fallback": info["fallback"] is not None,
             "long_prompt": {"above_tokens": LONG_PROMPT[model][0], "input_rate": LONG_PROMPT[model][1][0], "output_rate": LONG_PROMPT[model][1][1]}
             if model in LONG_PROMPT else None} for model, info in MODELS.items()]


def public_efforts() -> list[dict]:
    return [{"id": effort, "label": label} for effort, label in EFFORTS.items()]


def request_fields(settings: dict) -> dict:
    """What a Claude API job sends, taken from the saved AI settings; part of the job's input hash."""
    model = settings["claude_model"]
    return {"provider_model": model, "max_output_tokens": settings["claude_max_output_tokens"], "effort": settings["claude_effort"],
            "fallback": bool(settings["claude_fallback"]) and MODELS.get(model, {}).get("fallback") is not None,
            "settings_revision": settings["revision"]}


def _rates(model: str, prompt_tokens: int) -> tuple[float, float]:
    """Per-token rates for a model id as the API reports it. An unknown id is priced like the dearest model, so a receipt never undercounts."""
    known = model if model in RATES else max((name for name in RATES if model.startswith(name + "-")), key=len, default=None)
    if known is None:
        return max(RATES.values())
    long = LONG_PROMPT.get(known)
    return long[1] if long and prompt_tokens > long[0] else RATES[known]


def estimate_micro(request: dict, prompt: str) -> int:
    """The most one job may cost, in micro-USD. Every token covers at least one byte of UTF-8, so bytes bound the prompt."""
    model = request.get("provider_model")
    if model not in MODELS:
        raise DomainError("Model Claude chưa được hỗ trợ.", 409)
    output_ceiling = request.get("max_output_tokens")
    if type(output_ceiling) is not int or not OUTPUT_LIMITS[0] <= output_ceiling <= OUTPUT_LIMITS[1]:
        raise DomainError("Giới hạn output token không hợp lệ.", 409)
    # The margin covers message framing and the instructions the API adds for the schema and for thinking.
    input_ceiling = len(prompt.encode("utf-8")) + 4096
    if input_ceiling > 400_000:
        raise DomainError("Kịch bản quá dài cho một yêu cầu Claude; lưu thành từng phần rồi chạy lần lượt.", 409)
    runs = [_rates(model, input_ceiling)]
    if request.get("fallback") and MODELS[model]["fallback"]:
        runs.append(MODELS[model]["fallback"])
    return math.ceil(sum(input_ceiling * rate_in + output_ceiling * rate_out for rate_in, rate_out in runs))


def receipt_micro(message, requested: str) -> int:
    """Cost of an answered request from the usage Anthropic reports. After a fallback each attempt is listed with its model."""
    usage = message.usage
    runs = [(entry, getattr(entry, "model", None) or requested) for entry in (usage.iterations or [])] or [(usage, message.model or requested)]
    total = 0.0
    for entry, model in runs:
        plain, written, read, out = (getattr(entry, name, 0) or 0 for name in
                                     ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"))
        rate_in, rate_out = _rates(model, plain + written + read)
        total += plain * rate_in + written * rate_in * 1.25 + read * rate_in * 0.1 + out * rate_out
    return math.ceil(total)


def final_text(content) -> str:
    """The answer's text. After a fallback in mid-answer only the text after the last switch belongs to the final model."""
    blocks = list(content)
    start = max((index + 1 for index, block in enumerate(blocks) if block.type == "fallback"), default=0)
    return "".join(block.text for block in blocks[start:] if block.type == "text")


def _detail(exc: anthropic.APIStatusError) -> str:
    if exc.status_code in STATUS_HINTS:
        return STATUS_HINTS[exc.status_code]
    body = exc.body if isinstance(exc.body, dict) else {}
    error = body.get("error") if isinstance(body.get("error"), dict) else body
    text = str(error.get("message") or f"HTTP {exc.status_code}")[:300].strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def open_client(key: str) -> anthropic.Anthropic:
    """A client bound to Anthropic's own address, so a base-URL variable set for other tools cannot redirect the key."""
    return anthropic.Anthropic(api_key=key, base_url="https://api.anthropic.com", max_retries=0,
                               timeout=anthropic.Timeout(900.0, connect=20.0))


def key_configured() -> bool:
    try:
        return bool(get_provider_key(SECRET))
    except Exception:
        return False


class ClaudeAPI:
    def __init__(self, connect=None):
        self.connect = connect

    def _client(self):
        try:
            key = get_provider_key(SECRET)
        except Exception as exc:
            raise PlannerNotSent("Không đọc được khóa Claude API trong kho bảo mật Windows.", 409) from exc
        if not key:
            raise PlannerNotSent("Chưa có khóa Claude API trong kho bảo mật; vào Cài đặt → Dịch vụ AI để nhập khóa.", 409)
        return (self.connect or open_client)(key)

    def ask(self, request: dict, *, instructions: str, payload: dict, schema: dict) -> dict:
        """One streamed request answered as JSON that matches `schema`. Raises before returning anything unusable."""
        log.info("Request to %s: max_tokens=%d, effort=%s, fallback=%s, operation %s", request["provider_model"], request["max_output_tokens"],
                 request["effort"], request["fallback"], request.get("operation_id", "-"))
        started = time.monotonic()
        try:
            answer = self._ask(request, instructions=instructions, payload=payload, schema=schema)
        except PlannerAnswered as exc:
            log.warning("Answered by %s after %.1f s but unusable (cost $%.4f, request %s): %s", exc.model, time.monotonic() - started,
                        exc.actual_micro / 1e6, exc.request_id or "-", exc)
            raise
        except PlannerNotSent as exc:
            log.warning("Not run: %s", exc)
            raise
        except PlannerRemoteError as exc:
            log.error("Outcome unknown after %.1f s (request %s): %s", time.monotonic() - started, exc.request_id or "-", exc, exc_info=exc.__cause__)
            raise
        log.info("Answered by %s in %.1f s: %d input / %d output tokens, cost $%.4f, request %s", answer["model"], time.monotonic() - started,
                 *answer["tokens"], answer["actual_micro"] / 1e6, answer["request_id"])
        return answer

    def _ask(self, request: dict, *, instructions: str, payload: dict, schema: dict) -> dict:
        model = request["provider_model"]
        params = {"model": model, "max_tokens": request["max_output_tokens"], "system": instructions,
                  "messages": [{"role": "user", "content": canonical(payload)}], "thinking": {"type": "adaptive"},
                  "output_config": {"effort": request["effort"], "format": {"type": "json_schema", "schema": schema}}}
        if request["fallback"]:
            params.update(betas=[FALLBACK_BETA], fallbacks="default")
        client = self._client()
        opened, request_id = False, ""
        try:
            # Opening the stream sends the request; an error before that point means Claude did not run.
            with client.beta.messages.stream(**params) as stream:
                opened, request_id = True, stream.request_id or ""
                message = stream.get_final_message()
        except anthropic.APIStatusError as exc:
            request_id = exc.request_id or request_id
            if opened:
                raise PlannerRemoteError(f"Anthropic dừng giữa chừng: {_detail(exc)} Chưa rõ phần đã chạy bị tính bao nhiêu. Request ID: {request_id or 'không có'}. Không tự gửi lại.", request_id) from exc
            if exc.status_code < 500 or exc.status_code == 529:
                raise PlannerNotSent(f"Anthropic từ chối yêu cầu (HTTP {exc.status_code}): {_detail(exc)} Claude chưa chạy nên không tính phí.", 409) from exc
            raise PlannerRemoteError(f"Anthropic báo lỗi máy chủ (HTTP {exc.status_code}); chưa rõ yêu cầu đã chạy hay chưa. Request ID: {request_id or 'không có'}. Không tự gửi lại.", request_id) from exc
        except (anthropic.APIConnectionError, httpx2.TransportError) as exc:
            # The SDK wraps errors while sending; a connection dropped while reading the stream arrives unwrapped.
            cause = exc.__cause__ if isinstance(exc, anthropic.APIConnectionError) else exc
            if not opened and isinstance(cause, (httpx2.ConnectError, httpx2.ConnectTimeout)):
                raise PlannerNotSent("Không kết nối được Anthropic; yêu cầu chưa gửi. Kiểm tra mạng rồi thử lại.", 409) from exc
            raise PlannerRemoteError(f"Mất kết nối với Anthropic; chưa rõ yêu cầu đã chạy và bị tính phí hay chưa. Request ID: {request_id or 'không có'}. Không tự gửi lại.", request_id) from exc
        served, cost = message.model or model, receipt_micro(message, model)
        request_id = request_id or message.id
        if message.stop_reason == "refusal":
            category = message.stop_details.category if message.stop_details else None
            raise PlannerAnswered(f"Claude từ chối yêu cầu này theo chính sách an toàn (loại: {category or 'không rõ'}). Chi phí ghi theo usage Anthropic báo; sửa kịch bản hoặc chọn model khác rồi thử lại.",
                                  cost, request_id, served)
        if message.stop_reason == "max_tokens":
            raise PlannerAnswered("Kết quả bị cắt vì vượt giới hạn token đầu ra. Tăng giới hạn trong Cài đặt → Dịch vụ AI hoặc lưu kịch bản thành từng phần rồi chạy lần lượt.",
                                  cost, request_id, served)
        text = final_text(message.content)
        if message.stop_reason != "end_turn" or not text:
            raise PlannerAnswered(f"Claude dừng mà không có kết quả dùng được (lý do: {message.stop_reason or 'không rõ'}).", cost, request_id, served)
        usage = message.usage
        return {"text": text, "request_id": request_id, "actual_micro": cost, "model": served,
                "tokens": (usage.input_tokens or 0, usage.output_tokens or 0)}

    def check(self, model: str) -> dict:
        """Asks Anthropic whether the saved key may use the model. Generates nothing and costs nothing."""
        if model not in MODELS:
            raise DomainError("Model Claude chưa được hỗ trợ.", 409)
        client = self._client()
        try:
            info = client.models.retrieve(model)
        except anthropic.APIStatusError as exc:
            log.warning("Connection check for %s: HTTP %d (request %s)", model, exc.status_code, exc.request_id or "-")
            raise DomainError(f"Anthropic trả HTTP {exc.status_code} khi kiểm tra: {_detail(exc)}", 409) from exc
        except anthropic.APIConnectionError as exc:
            log.warning("Connection check for %s: no connection (%s)", model, type(exc.__cause__ or exc).__name__)
            raise DomainError("Không kết nối được Anthropic; kiểm tra mạng và thử lại.", 409) from exc
        log.info("Connection check for %s: the key may use it", model)
        return {"reachable": True, "model": info.id,
                "message": f"Anthropic nhận khóa và tài khoản dùng được {info.display_name}. Lần kiểm tra này không tạo nội dung, không tính phí và chưa chứng minh một lần phân tích thành công."}


class ClaudeAPIExtractor:
    """S2 listing through the Claude API. Paid, so it passes the budget gate and lands in the cost ledger."""

    kind = "CLAUDE_API"
    paid = True
    note = NOTE

    def __init__(self, api: ClaudeAPI | None = None):
        self.api = api or ClaudeAPI()

    @staticmethod
    def _payload(request: dict) -> dict:
        return {"language": request["language"], "screenplay_text": request["screenplay_text"]}

    def estimate_micro(self, request: dict) -> int:
        return estimate_micro(request, EXTRACTION_INSTRUCTIONS + canonical(EXTRACTION_SCHEMA) + canonical(self._payload(request)))

    def extract(self, request: dict) -> ExtractResult:
        self.estimate_micro(request)
        answer = self.api.ask(request, instructions=EXTRACTION_INSTRUCTIONS, payload=self._payload(request), schema=EXTRACTION_SCHEMA)
        return ExtractResult(answer["text"], answer["request_id"], answer["actual_micro"], answer["model"])


class ClaudeAPIBreakdown:
    """Step 03 scene and shot draft through the Claude API. Paid, so it passes the budget gate and lands in the cost ledger."""

    kind = "CLAUDE_API"
    paid = True
    note = NOTE

    def __init__(self, api: ClaudeAPI | None = None):
        self.api = api or ClaudeAPI()

    @staticmethod
    def _payload(request: dict) -> dict:
        return {key: request[key] for key in INPUT_KEYS}

    def estimate_micro(self, request: dict) -> int:
        return estimate_micro(request, BREAKDOWN_INSTRUCTIONS + canonical(DRAFT_SCHEMA) + canonical(self._payload(request)))

    def generate(self, request: dict) -> BreakdownResult:
        self.estimate_micro(request)
        answer = self.api.ask(request, instructions=BREAKDOWN_INSTRUCTIONS, payload=self._payload(request), schema=DRAFT_SCHEMA)
        return BreakdownResult(answer["text"], answer["request_id"], answer["actual_micro"], answer["model"])
