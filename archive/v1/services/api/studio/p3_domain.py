"""Versioned voice timing and capability-gated UE performance plans."""

from __future__ import annotations

from .domain import DomainError


def duration_frames(sample_count: int, sample_rate: int, fps: dict) -> int:
    if min(sample_count, sample_rate, fps["num"], fps["den"]) <= 0:
        raise DomainError("Thông số WAV/FPS không hợp lệ.")
    numerator = sample_count * fps["num"]
    denominator = sample_rate * fps["den"]
    return (numerator + denominator - 1) // denominator


def resolve_camera_intent(manifest: dict) -> dict:
    intent = manifest.get("camera_intent") or {}
    mode = intent.get("mode")
    performers = {p["character_id"] for p in manifest["performances"]}
    subject = intent.get("subject")
    second = intent.get("secondary_subject")
    if mode not in ("static_dialogue", "follow_walking", "two_shot"):
        raise DomainError("Chưa có mode camera được hỗ trợ hoặc đã kiểm chứng trong manifest.", 409)
    if mode in ("static_dialogue", "follow_walking") and (subject not in performers or second):
        raise DomainError("Camera cần đúng một subject đang diễn trong cảnh.", 409)
    if mode == "static_dialogue" and not any(p.get("character_id") == subject and p.get("dialogue_id") for p in manifest["performances"]):
        raise DomainError("Static dialogue cần subject có thoại.", 409)
    if mode == "two_shot" and (not subject or not second or subject == second or {subject, second} - performers):
        raise DomainError("Two-shot cần hai subject khác nhau đang diễn trong cảnh.", 409)
    return {"schema_version": "p3.camera-plan.1", "mode": mode, "subjects": [subject, second] if second else [subject],
            "shot_size": intent.get("shot_size"), "ue_properties": None,
            "capability": "BLOCKED_P0_BLACK_EYE_UNVERIFIED"}


class FacialAdapter:
    kind = "UNREAL_FACIAL_UNAVAILABLE"

    def preflight(self, _plan: dict) -> None:
        raise DomainError("P0 chưa xác nhận MetaHuman/facial solver version và API bake/track trong UE thật.", 409)

    def execute(self, _plan: dict) -> None:
        self.preflight(_plan)


class CameraAdapter:
    kind = "UNREAL_CAMERA_UNAVAILABLE"

    def preflight(self, _plan: dict) -> None:
        raise DomainError("P0 chưa xác nhận Black Eye version/property inventory trong UE thật.", 409)

    def execute(self, _plan: dict) -> None:
        self.preflight(_plan)
