"""Voice provider contract and a clearly synthetic local control-flow fixture."""

from __future__ import annotations

import hashlib
import io
import math
import struct
import wave
from fractions import Fraction

from .domain import DomainError


class LocalFixtureVoiceProvider:
    kind = "LOCAL_VOICE_FIXTURE"

    def render(self, request: dict) -> tuple[bytes, dict]:
        if request.get("provider_kind") != self.kind:
            raise DomainError("Provider live chưa được cấu hình và duyệt ngân sách.", 409)
        sample_rate = request["sample_rate"]
        sample_count = sample_rate * request["fixture_duration_ms"] // 1000
        if sample_count <= 0 or sample_count > sample_rate * 60:
            raise DomainError("Fixture voice phải dài từ 1 sample đến 60 giây.")
        # This tone is deliberately not speech and cannot be lip-sync evidence.
        tone = b"".join(struct.pack("<h", int(1200 * math.sin(2 * math.pi * 440 * i / sample_rate)))
                        for i in range(sample_count))
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(tone)
        data = buffer.getvalue()
        return data, {"adapter_kind": self.kind, "provider_id": "local-fixture-v1",
                      "provider_request_id": "fixture:" + request["operation_id"],
                      "sample_rate": sample_rate, "sample_count": sample_count,
                      "duration_seconds": str(Fraction(sample_count, sample_rate)),
                      "wav_sha256": hashlib.sha256(data).hexdigest(), "audio_kind": "SYNTHETIC_TONE_NOT_SPEECH"}


def inspect_wav(data: bytes) -> tuple[int, int]:
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getcomptype() != "NONE":
                raise DomainError("WAV cần PCM mono 16-bit.")
            return wav.getframerate(), wav.getnframes()
    except (wave.Error, EOFError) as exc:
        raise DomainError("WAV không hợp lệ.") from exc
