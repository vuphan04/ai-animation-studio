from __future__ import annotations

import hashlib
import json
import re
from fractions import Fraction
from pathlib import Path


SUBSYSTEMS = ("body", "face", "audio", "camera", "environment", "hair", "cloth", "vfx", "render", "sound")
ROUTES = ("UNREAL", "SEEDANCE", "HYBRID")
DEPENDENT = {
    "body": ("hair", "cloth", "vfx", "render"),
    "face": ("render",),
    "audio": ("render", "sound"),
    "camera": ("render",),
    "environment": ("hair", "cloth", "vfx", "render"),
    "hair": ("render",),
    "cloth": ("render",),
    "vfx": ("render",),
    "render": ("sound",),
}
FORBIDDEN = {"approved", "approval", "ue_path", "asset_path", "bone_map", "transform", "api_key", "secret"}
TOP_FIELDS = {"schema_version", "shot_id", "fps", "timeline", "route", "environment_id", "performances", "intentional_overlap_with", "camera_intent", "render_profile_id"}
PERFORMANCE_FIELDS = {"performance_id", "character_id", "anchor_id", "animation_id", "retarget_profile_id", "paired_asset_id", "dialogue_id", "voice_profile_id", "dialogue_start_local_frame"}


class DomainError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def canonical(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(data: object) -> str:
    return hashlib.sha256(canonical(data).encode("utf-8")).hexdigest()


def safe_relative(root: Path, value: str) -> str:
    path = Path(value)
    if path.is_absolute() or not value or ":" in value or "\\" in value:
        raise DomainError("relative_path must be relative to the project root")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()) or resolved == root.resolve():
        raise DomainError("relative_path escapes project root")
    return resolved.relative_to(root.resolve()).as_posix()


def _walk_forbidden(value: object, location: str = "manifest") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in FORBIDDEN:
                raise DomainError(f"{location}.{key} is not allowed in a draft manifest")
            _walk_forbidden(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_forbidden(child, f"{location}[{index}]")


def _only_fields(value: dict, allowed: set[str], location: str) -> None:
    extra = set(value) - allowed
    if extra:
        raise DomainError(f"{location}: unsupported field {sorted(extra)[0]}")


def validate_manifest(manifest: dict, registry: dict[str, dict], siblings: list[dict]) -> dict:
    if not isinstance(manifest, dict):
        raise DomainError("manifest must be an object")
    _walk_forbidden(manifest)
    _only_fields(manifest, TOP_FIELDS, "manifest")
    if manifest.get("schema_version") != "1.0":
        raise DomainError("schema_version must be 1.0")
    if not isinstance(manifest.get("shot_id"), str) or not manifest["shot_id"]:
        raise DomainError("shot_id is required")
    fps, timeline = manifest.get("fps"), manifest.get("timeline")
    if not isinstance(fps, dict) or type(fps.get("num")) is not int or type(fps.get("den")) is not int or fps["num"] <= 0 or fps["den"] <= 0:
        raise DomainError("fps.num and fps.den must be positive integers")
    _only_fields(fps, {"num", "den"}, "fps")
    if not isinstance(timeline, dict) or type(timeline.get("in")) is not int or type(timeline.get("out_exclusive")) is not int or timeline["in"] < 0 or timeline["out_exclusive"] <= timeline["in"]:
        raise DomainError("timeline must be a nonempty [in,out_exclusive) frame range")
    _only_fields(timeline, {"in", "out_exclusive"}, "timeline")
    route = manifest.get("route")
    if not isinstance(route, dict) or route.get("suggested") not in ROUTES:
        raise DomainError("route.suggested must be UNREAL, SEEDANCE or HYBRID")
    _only_fields(route, {"suggested"}, "route")
    if not isinstance(manifest.get("performances"), list):
        raise DomainError("performances must be an array")
    duration = timeline["out_exclusive"] - timeline["in"]

    def known(asset_id: object, kind: str, field: str) -> dict:
        item = registry.get(asset_id) if isinstance(asset_id, str) else None
        if item is None or item["kind"] != kind:
            raise DomainError(f"{field}: unknown or wrong-kind registry ID {asset_id!r}")
        return item

    known(manifest.get("environment_id"), "environment", "environment_id")
    if manifest.get("render_profile_id") is not None:
        known(manifest["render_profile_id"], "render_profile", "render_profile_id")
    camera_intent = manifest.get("camera_intent")
    if camera_intent is not None:
        if not isinstance(camera_intent, dict):
            raise DomainError("camera_intent must be an object")
        _only_fields(camera_intent, {"mode", "shot_size", "subject", "secondary_subject"}, "camera_intent")
        if "mode" in camera_intent and camera_intent["mode"] not in ("static_dialogue", "follow_walking", "two_shot"):
            raise DomainError("camera_intent.mode unsupported")
        if "shot_size" in camera_intent and (not isinstance(camera_intent["shot_size"], str) or not camera_intent["shot_size"]):
            raise DomainError("camera_intent.shot_size must be a nonempty string")
        if "subject" in camera_intent:
            known(camera_intent["subject"], "character", "camera_intent.subject")
        if "secondary_subject" in camera_intent:
            known(camera_intent["secondary_subject"], "character", "camera_intent.secondary_subject")
    performance_ids: set[str] = set()
    for i, performance in enumerate(manifest["performances"]):
        if not isinstance(performance, dict):
            raise DomainError(f"performances[{i}] must be an object")
        _only_fields(performance, PERFORMANCE_FIELDS, f"performances[{i}]")
        performance_id = performance.get("performance_id")
        if performance_id is not None:
            if not isinstance(performance_id, str) or not re.fullmatch(r"[A-Za-z0-9_]+", performance_id) or performance_id in performance_ids:
                raise DomainError(f"performances[{i}].performance_id missing or duplicate")
            performance_ids.add(performance_id)
        character = known(performance.get("character_id"), "character", f"performances[{i}].character_id")
        known(performance.get("anchor_id"), "anchor", f"performances[{i}].anchor_id")
        animation = known(performance.get("animation_id"), "animation", f"performances[{i}].animation_id")
        targets = animation["metadata"].get("compatible_skeletons", [])
        skeleton = character["metadata"].get("skeleton_id")
        if skeleton is None:
            raise DomainError(f"performances[{i}].character_id missing skeleton")
        if skeleton not in targets:
            profile_id = performance.get("retarget_profile_id")
            if not profile_id:
                raise DomainError(f"performances[{i}].animation_id incompatible with character skeleton")
            profile = known(profile_id, "retarget_profile", f"performances[{i}].retarget_profile_id")
            source = animation["metadata"].get("source_skeleton_id")
            if source not in targets or profile["metadata"].get("source_skeleton_id") != source or profile["metadata"].get("target_skeleton_id") != skeleton:
                raise DomainError(f"performances[{i}].retarget_profile_id incompatible skeleton pair")
        if performance.get("paired_asset_id"):
            known(performance["paired_asset_id"], "paired_animation", f"performances[{i}].paired_asset_id")
        dialogue_id = performance.get("dialogue_id")
        if dialogue_id is None and "dialogue_start_local_frame" in performance:
            raise DomainError(f"performances[{i}].dialogue_start_local_frame requires dialogue_id")
        if dialogue_id is None and "voice_profile_id" in performance:
            raise DomainError(f"performances[{i}].voice_profile_id requires dialogue_id")
        if dialogue_id is not None:
            dialogue = known(dialogue_id, "dialogue", f"performances[{i}].dialogue_id")
            if performance.get("voice_profile_id"):
                if not performance_id:
                    raise DomainError(f"performances[{i}].performance_id required for voice")
                voice_profile = known(performance["voice_profile_id"], "voice_profile", f"performances[{i}].voice_profile_id")
                if voice_profile["metadata"].get("character_id") != performance["character_id"]:
                    raise DomainError(f"performances[{i}].voice_profile_id incompatible character")
                if dialogue["metadata"].get("character_id") != performance["character_id"] or not dialogue["metadata"].get("text"):
                    raise DomainError(f"performances[{i}].dialogue character/text mismatch")
            start = performance.get("dialogue_start_local_frame")
            frames = dialogue["metadata"].get("duration_frames")
            if type(start) is not int or start < 0 or type(frames) is not int or frames <= 0:
                raise DomainError(f"performances[{i}].dialogue timing is incomplete")
            if start + frames > duration:
                raise DomainError(f"performances[{i}].dialogue overflows shot by {start + frames - duration} frames")
    intended = manifest.get("intentional_overlap_with", [])
    if not isinstance(intended, list) or any(not isinstance(code, str) for code in intended):
        raise DomainError("intentional_overlap_with must be an array of shot IDs")
    sibling_ids = {sibling["shot_id"] for sibling in siblings}
    if len(set(intended)) != len(intended) or any(code not in sibling_ids for code in intended):
        raise DomainError("intentional_overlap_with contains duplicate or unknown shot ID")
    for sibling in siblings:
        if sibling["fps"] != fps:
            raise DomainError(f"fps differs from adjacent shot {sibling['shot_id']}")
        other = sibling["timeline"]
        if timeline["in"] < other["out_exclusive"] and other["in"] < timeline["out_exclusive"] and sibling["shot_id"] not in intended:
            raise DomainError(f"timeline overlaps {sibling['shot_id']} without intentional_overlap_with")
    return {"frame_count": duration, "duration_seconds": str(Fraction(duration * fps["den"], fps["num"])), "fps": fps}
