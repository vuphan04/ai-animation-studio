"""AI video providers for workflow V4.5 drafts: Seedance on Ark (BytePlus or Volcengine) and MiniMax.

Request shapes follow the providers' published task APIs. They have not been exercised against a live
account by this codebase; the first real draft is what verifies them.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx

from .domain import DomainError


MAX_VIDEO_BYTES = 1_000_000_000
MAX_IMAGE_BYTES = 10_000_000


class VideoNotSent(DomainError):
    """Nothing was created: the request never left, or the provider refused it."""


class VideoOutcomeUnknown(DomainError):
    """The request was sent and no usable answer came back. A task may exist and may be billed."""


@dataclass(frozen=True)
class VideoPoll:
    state: str  # PENDING, SUCCEEDED, FAILED
    video_url: str | None = None
    error: str | None = None


def data_url(path: str) -> str:
    file = Path(path)
    if not file.is_file() or file.stat().st_size > MAX_IMAGE_BYTES:
        raise VideoNotSent("Ảnh tham chiếu thiếu hoặc lớn hơn 10 MB; yêu cầu chưa gửi.", 409)
    mime = "image/png" if file.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(file.read_bytes()).decode("ascii")


class _HttpProvider:
    supports_control_video = False  # No verified way to hand the provider a local control video yet.

    def __init__(self, kind: str, base_url: str, key_reader, transport=None):
        self.kind = kind
        self.base_url = base_url
        self.key_reader = key_reader
        self.transport = transport

    def _client(self, timeout: float) -> httpx.Client:
        return httpx.Client(transport=self.transport, timeout=httpx.Timeout(timeout, connect=15), trust_env=False)

    def _key(self) -> str:
        try:
            key = self.key_reader()
        except Exception:
            key = None
        if not key:
            raise VideoNotSent("Chưa có khóa của nhà cung cấp video trong kho bảo mật Windows; yêu cầu chưa gửi.", 409)
        return key

    @staticmethod
    def _reason(response) -> str:
        """Short provider message for the operator; never includes request headers."""
        try:
            body = response.json()
        except ValueError:
            return ""
        error = body.get("error") if isinstance(body, dict) else None
        message = (error.get("message") or error.get("code")) if isinstance(error, dict) else (body.get("base_resp") or {}).get("status_msg") if isinstance(body, dict) else None
        return f" · {str(message)[:160]}" if message else ""

    def _post(self, path: str, body: dict) -> dict:
        key = self._key()
        try:
            with self._client(120) as client:
                response = client.post(self.base_url + path, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, json=body)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise VideoNotSent("Không kết nối được nhà cung cấp video; yêu cầu chưa gửi.", 409) from exc
        except httpx.HTTPError as exc:
            raise VideoOutcomeUnknown("Mất kết nối sau khi gửi; chưa rõ nhà cung cấp đã tạo task và tính phí hay chưa.", 409) from exc
        if response.status_code >= 400:
            raise VideoNotSent(f"Nhà cung cấp video từ chối yêu cầu (HTTP {response.status_code}{self._reason(response)}); không có task nào được tạo.", 409)
        try:
            return response.json()
        except ValueError as exc:
            raise VideoOutcomeUnknown("Nhà cung cấp trả lời không đọc được sau khi nhận yêu cầu; chưa rõ task đã được tạo hay chưa.", 409) from exc

    def _get(self, path: str) -> dict:
        """Status queries create nothing, so any failure here simply means: ask again later."""
        with self._client(60) as client:
            response = client.get(self.base_url + path, headers={"Authorization": "Bearer " + self._key()})
        if response.status_code >= 400:
            raise DomainError(f"Truy vấn task video lỗi HTTP {response.status_code}{self._reason(response)}.", 409)
        return response.json()

    def download(self, url: str, target: Path) -> None:
        part = target.with_suffix(".part")
        size = 0
        try:
            with self._client(600) as client, client.stream("GET", url) as response:
                if response.status_code != 200:
                    raise DomainError(f"Tải video từ nhà cung cấp lỗi HTTP {response.status_code}.", 409)
                with open(part, "wb") as stream:
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > MAX_VIDEO_BYTES:
                            raise DomainError("Video trả về lớn hơn 1 GB; không lưu.", 409)
                        stream.write(chunk)
            os.replace(part, target)
        finally:
            part.unlink(missing_ok=True)


class SeedanceProvider(_HttpProvider):
    """Ark video generation tasks. A draft is a 480p task whose ID can later be upgraded to the final."""
    draft_resolution = "480p"
    draft_expiry_days = 7
    max_references = 4
    final_resolution = "1080p"
    supports_final_from_draft = True

    def submit(self, request: dict) -> str:
        content = [{"type": "text", "text": request["prompt"]}]
        if request["first_frame"]:
            # First-frame and reference-image modes cannot be combined in one task.
            content.append({"type": "image_url", "image_url": {"url": data_url(request["first_frame"]["path"])}, "role": "first_frame"})
        else:
            content += [{"type": "image_url", "image_url": {"url": data_url(item["path"])}, "role": "reference_image"} for item in request["references"]]
        answer = self._post("/api/v3/contents/generations/tasks", {
            "model": request["model_id"], "content": content, "resolution": self.draft_resolution, "ratio": request["ratio"],
            "duration": request["seconds"], "watermark": False, "generate_audio": False, "draft": True})
        return self._task_id(answer)

    @staticmethod
    def _task_id(answer: object) -> str:
        if not isinstance(answer, dict) or not isinstance(answer.get("id"), str) or not answer["id"]:
            raise VideoOutcomeUnknown("Nhà cung cấp nhận yêu cầu nhưng không trả ID task; kiểm tra tài khoản trước khi gửi lại.", 409)
        return answer["id"]

    def submit_final(self, request: dict) -> str:
        """The final is inferred again from the draft task: same prompt, references and seed, at 1080p.
        Composition and motion are kept; it is not a pixel copy of the draft."""
        return self._task_id(self._post("/api/v3/contents/generations/tasks", {
            "model": request["model_id"], "content": [{"type": "draft_task", "draft_task": {"id": request["draft_task_id"]}}],
            "resolution": self.final_resolution, "watermark": False}))

    def poll(self, task_id: str) -> VideoPoll:
        answer = self._get("/api/v3/contents/generations/tasks/" + quote(task_id, safe=""))
        status = str(answer.get("status", "")).lower()
        if status == "succeeded":
            url = (answer.get("content") or {}).get("video_url")
            return VideoPoll("SUCCEEDED", url) if url else VideoPoll("FAILED", error="Task hoàn tất nhưng không có đường dẫn video.")
        if status in ("failed", "cancelled", "expired"):
            return VideoPoll("FAILED", error=str((answer.get("error") or {}).get("message") or status)[:300])
        return VideoPoll("PENDING")


class MiniMaxProvider(_HttpProvider):
    """MiniMax video generation. The draft is a 768P clip; references other than a first frame are not sent."""
    draft_resolution = "768P"
    draft_expiry_days = None
    max_references = 0
    final_resolution = "2K"
    # The 768P → 2K regenerate call has no request specification this app can rely on yet.
    supports_final_from_draft = False

    def submit_final(self, request: dict) -> str:
        raise VideoNotSent("App chưa có đặc tả API regenerate 768P → 2K của MiniMax H3; yêu cầu chưa gửi.", 409)

    def submit(self, request: dict) -> str:
        body = {"model": request["model_id"], "prompt": request["prompt"], "duration": request["seconds"], "resolution": self.draft_resolution}
        if request["first_frame"]:
            body["first_frame_image"] = data_url(request["first_frame"]["path"])
        answer = self._post("/v1/video_generation", body)
        status = (answer.get("base_resp") or {}) if isinstance(answer, dict) else {}
        if status.get("status_code") not in (0, None):
            raise VideoNotSent(f"MiniMax từ chối yêu cầu ({str(status.get('status_msg'))[:160]}); không có task nào được tạo.", 409)
        if not isinstance(answer, dict) or not answer.get("task_id"):
            raise VideoOutcomeUnknown("MiniMax nhận yêu cầu nhưng không trả ID task; kiểm tra tài khoản trước khi gửi lại.", 409)
        return str(answer["task_id"])

    def poll(self, task_id: str) -> VideoPoll:
        answer = self._get("/v1/query/video_generation?task_id=" + quote(task_id, safe=""))
        status = str(answer.get("status", "")).lower()
        if status == "success":
            file = self._get("/v1/files/retrieve?file_id=" + quote(str(answer.get("file_id", "")), safe=""))
            url = (file.get("file") or {}).get("download_url")
            return VideoPoll("SUCCEEDED", url) if url else VideoPoll("FAILED", error="Task hoàn tất nhưng không có đường dẫn tải video.")
        if status == "fail":
            return VideoPoll("FAILED", error=str((answer.get("base_resp") or {}).get("status_msg") or "fail")[:300])
        return VideoPoll("PENDING")
