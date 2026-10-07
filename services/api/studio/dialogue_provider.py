"""Speech for dialogue lines: ElevenLabs text-to-speech, and a synthetic tone for control-flow tests.

Audio length is always measured from the returned samples, never estimated from the text.
"""

from __future__ import annotations

import io
import math
import struct
import wave
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from .domain import DomainError
from .secrets import get_provider_key
from .voice_settings import BASE_URL, OUTPUT_FORMATS, PROVIDER


def inspect_wav(data: bytes) -> tuple[int, int]:
    """Sample rate and sample count of a mono 16-bit PCM WAV; anything else is refused."""
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getcomptype() != "NONE":
                raise DomainError("WAV cần PCM mono 16-bit.")
            return wav.getframerate(), wav.getnframes()
    except (wave.Error, EOFError) as exc:
        raise DomainError("WAV không hợp lệ.") from exc


class DialogueNotSent(DomainError):
    """The request never left this machine, or the provider answered with a refusal. Nothing was generated."""


class DialogueOutcomeUnknown(DomainError):
    """The request was sent and no answer came back. It may have been generated and billed."""


@dataclass(frozen=True)
class DialogueAudio:
    wav: bytes
    sample_rate: int
    sample_count: int
    audio_kind: str
    provider_request_id: str | None = None
    billed_characters: int | None = None


def pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buffer.getvalue()


class FixtureDialogueProvider:
    """A tone whose length follows the word count. It is not speech and proves nothing about a voice."""
    kind = "LOCAL_VOICE_FIXTURE"
    paid = False

    def render(self, request: dict) -> DialogueAudio:
        sample_rate = request["sample_rate"]
        seconds = min(max(0.6 + 0.38 * len(request["text"].split()), 0.8), 30.0)
        count = int(sample_rate * seconds)
        pcm = b"".join(struct.pack("<h", int(1200 * math.sin(2 * math.pi * 440 * i / sample_rate))) for i in range(count))
        return DialogueAudio(pcm_to_wav(pcm, sample_rate), sample_rate, count, "SYNTHETIC_TONE_NOT_SPEECH", "fixture:" + request["operation_id"][:16])


class ElevenLabsDialogueProvider:
    kind = PROVIDER
    paid = True

    def __init__(self, transport=None, key_reader=None):
        self.transport = transport
        self.key_reader = key_reader or (lambda: get_provider_key(PROVIDER))

    def render(self, request: dict) -> DialogueAudio:
        try:
            key = self.key_reader()
        except Exception:
            key = None
        if not key:
            raise DialogueNotSent("Chưa có khóa ElevenLabs trong kho bảo mật Windows; yêu cầu chưa gửi.", 409)
        sample_rate = OUTPUT_FORMATS[request["output_format"]]
        body = {"text": request["text"], "model_id": request["model_id"]}
        settings = request["settings"]
        if settings:
            # The API expects stability and similarity together; an unset one keeps ElevenLabs' documented default.
            body["voice_settings"] = {"stability": settings.get("stability", 0.5), "similarity_boost": settings.get("similarity_boost", 0.75),
                                      **({"speed": settings["speed"]} if "speed" in settings else {})}
        try:
            with httpx.Client(transport=self.transport, timeout=httpx.Timeout(180, connect=15), trust_env=False) as client:
                response = client.post(f"{BASE_URL}/v1/text-to-speech/{quote(request['voice_id'], safe='')}",
                                       params={"output_format": request["output_format"]},
                                       headers={"xi-api-key": key, "content-type": "application/json"}, json=body)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise DialogueNotSent("Không kết nối được ElevenLabs; yêu cầu chưa gửi.", 409) from exc
        except httpx.HTTPError as exc:
            raise DialogueOutcomeUnknown("Mất kết nối sau khi gửi; chưa rõ ElevenLabs đã tạo thoại và tính phí hay chưa.", 409) from exc
        if response.status_code != 200:
            raise DialogueNotSent(f"ElevenLabs từ chối yêu cầu (HTTP {response.status_code}{self._reason(response)}); không có thoại nào được tạo.", 409)
        pcm = response.content
        if not pcm or len(pcm) % 2:
            raise DomainError("ElevenLabs trả dữ liệu âm thanh không đúng dạng PCM 16-bit; không lưu take.", 409)
        billed = response.headers.get("character-cost", "")
        return DialogueAudio(pcm_to_wav(pcm, sample_rate), sample_rate, len(pcm) // 2, "SPEECH",
                             response.headers.get("request-id"), int(billed) if billed.isdigit() else None)

    @staticmethod
    def _reason(response) -> str:
        """Short provider status for the operator. Never echoes request headers, so the key cannot leak."""
        try:
            detail = response.json().get("detail")
            status = detail.get("status") if isinstance(detail, dict) else None
            return f" · {status[:60]}" if isinstance(status, str) and status else ""
        except (ValueError, AttributeError):
            return ""
