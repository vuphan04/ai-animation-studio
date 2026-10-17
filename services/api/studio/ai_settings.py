"""Operator-facing AI configuration. Secret values never enter HTTP or SQLite."""

from __future__ import annotations

from . import claude_api
from .domain import DomainError
from .secrets import delete_provider_key, get_provider_key, prompt_provider_key
from .service import now
from .model_catalog import MODEL_CATALOG, public_models


MODELS = set(MODEL_CATALOG)
PROVIDERS = {"LOCAL_PLANNER_FIXTURE", "OPENAI", "CLAUDE_CODE", "CLAUDE_API"}
# The vault entry that holds each paid provider's key, and what to say when it is missing.
SECRETS = {"OPENAI": "OPENAI", "CLAUDE_API": claude_api.SECRET}
KEY_MISSING = {"OPENAI": "Chưa có khóa OpenAI trong kho bảo mật; vào Cài đặt để nhập khóa.",
               "CLAUDE_API": "Chưa có khóa Claude API trong kho bảo mật; vào Cài đặt → Dịch vụ AI để nhập khóa."}
COLUMNS = "revision,provider_kind,model,max_output_tokens,claude_model,claude_effort,claude_max_output_tokens,claude_fallback,updated_at"
CLAUDE_FIELDS = ("claude_model", "claude_effort", "claude_max_output_tokens", "claude_fallback")


def key_available(provider_kind: str) -> bool:
    try:
        return bool(get_provider_key(SECRETS[provider_kind]))
    except Exception:
        return False


class AISettings:
    def __init__(self, studio, claude_code=None):
        self.db = studio.db
        self.claude_code = claude_code

    def get(self) -> dict:
        with self.db.connect() as conn:
            value = dict(conn.execute(f"SELECT {COLUMNS} FROM ai_settings WHERE id=1").fetchone())
        value["claude_fallback"] = bool(value["claude_fallback"])
        value["key_configured"] = key_available("OPENAI")
        value["claude_key_configured"] = key_available("CLAUDE_API")
        if value["provider_kind"] == "CLAUDE_CODE":
            # Found on this machine; whether it is signed in and answers is shown by its own panel.
            value["configured"] = bool(self.claude_code and self.claude_code.available())
        elif value["provider_kind"] == "CLAUDE_API":
            value["configured"] = value["claude_key_configured"]
        else:
            value["configured"] = value["provider_kind"] == "LOCAL_PLANNER_FIXTURE" or value["key_configured"]
        value["available_models"] = public_models()
        value["claude_models"] = claude_api.public_models()
        value["claude_efforts"] = claude_api.public_efforts()
        return value

    def save(self, provider_kind: str, model: str, max_output_tokens: int, expected_revision: int, **claude) -> dict:
        """`claude` holds the Claude API fields; a field left out keeps its saved value."""
        if provider_kind not in PROVIDERS or model not in MODELS or type(max_output_tokens) is not int or not 512 <= max_output_tokens <= 16384:
            raise DomainError("Provider, model hoặc giới hạn output không hợp lệ.")
        if set(claude) - set(CLAUDE_FIELDS):
            raise DomainError("Provider, model hoặc giới hạn output không hợp lệ.")
        with self.db.transaction() as conn:
            current = dict(conn.execute(f"SELECT {COLUMNS} FROM ai_settings WHERE id=1").fetchone())
            if current["revision"] != expected_revision:
                raise DomainError("Cấu hình AI đã đổi ở phiên khác; tải lại trước khi lưu.", 409)
            chosen = {field: claude[field] if claude.get(field) is not None else current[field] for field in CLAUDE_FIELDS}
            chosen["claude_fallback"] = bool(chosen["claude_fallback"])
            tokens = chosen["claude_max_output_tokens"]
            if (chosen["claude_model"] not in claude_api.MODELS or chosen["claude_effort"] not in claude_api.EFFORTS
                    or type(tokens) is not int or not claude_api.OUTPUT_LIMITS[0] <= tokens <= claude_api.OUTPUT_LIMITS[1]):
                raise DomainError("Model, mức suy nghĩ hoặc giới hạn token của Claude API không hợp lệ.")
            conn.execute("UPDATE ai_settings SET revision=?,provider_kind=?,model=?,max_output_tokens=?,claude_model=?,claude_effort=?,"
                         "claude_max_output_tokens=?,claude_fallback=?,updated_at=? WHERE id=1",
                         (current["revision"] + 1, provider_kind, model, max_output_tokens, chosen["claude_model"], chosen["claude_effort"],
                          tokens, int(chosen["claude_fallback"]), now()))
        return self.get()

    def prompt_key(self, provider_kind: str = "OPENAI") -> dict:
        if provider_kind not in SECRETS:
            raise DomainError("Nhà cung cấp khóa chưa được hỗ trợ.")
        try:
            prompt_provider_key(SECRETS[provider_kind])
        except RuntimeError as exc:
            raise DomainError(str(exc), 409) from exc
        except Exception as exc:
            raise DomainError("Không lưu được khóa vào Windows Credential Manager.", 409) from exc
        with self.db.transaction() as conn:
            conn.execute("UPDATE ai_settings SET revision=revision+1,updated_at=? WHERE id=1", (now(),))
        return self.get()

    def delete_key(self, provider_kind: str = "OPENAI") -> dict:
        if provider_kind not in SECRETS:
            raise DomainError("Nhà cung cấp khóa chưa được hỗ trợ.")
        try:
            delete_provider_key(SECRETS[provider_kind])
        except Exception as exc:
            raise DomainError("Kho khóa Windows không khả dụng trên máy này.", 409) from exc
        with self.db.transaction() as conn:
            conn.execute("UPDATE ai_settings SET revision=revision+1,updated_at=? WHERE id=1", (now(),))
        return self.get()
