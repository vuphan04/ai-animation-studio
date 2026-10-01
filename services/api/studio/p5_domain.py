"""Planner response validation and screenplay-as-data rules."""

from __future__ import annotations

import json
import re

from .domain import DomainError, validate_manifest


SCHEMA_VERSION = "p5.draft.1"
FORBIDDEN_KEYS = {"approved", "approval", "lock", "locked", "lock_state", "ue_path", "asset_path",
                  "resolved_path", "resolved_registry", "bone_map", "transform", "api_key", "secret",
                  "command", "shell_command", "execute", "skip_checkpoints"}


def _only_fields(value: object, fields: set[str], required: set[str], location: str) -> dict:
    if not isinstance(value, dict):
        raise DomainError(f"{location} phải là JSON object.")
    extra = set(value) - fields
    missing = required - set(value)
    if extra or missing:
        raise DomainError(f"{location}: trường không hợp lệ hoặc thiếu: {sorted(extra or missing)[0]}")
    return value


def _forbidden_keys(value: object, path: str = "draft") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in FORBIDDEN_KEYS:
                raise DomainError(f"Planner không được cấp {path}.{key}; chỉ người dùng/backend có quyền duyệt, khóa và gắn asset path.", 409)
            _forbidden_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _forbidden_keys(child, f"{path}[{index}]")


def parse_response(raw: str) -> dict:
    if not isinstance(raw, str) or len(raw) > 250_000:
        raise DomainError("Planner response không phải JSON text hợp lệ.")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DomainError(f"Planner JSON lỗi ở vị trí {exc.pos}.") from exc
    _forbidden_keys(value)
    return _only_fields(value, {"schema_version", "scenes", "shots", "unresolved_requirements"},
                        {"schema_version", "scenes", "shots", "unresolved_requirements"}, "draft")


def validate_draft(value: dict, registry: dict[str, dict], *, screenplay_id: str,
                   fps: dict, existing_codes: set[str], estimated_seconds: int) -> dict:
    if value["schema_version"] != SCHEMA_VERSION:
        raise DomainError("Planner schema_version phải là p5.draft.1.")
    scenes = value["scenes"]
    shots = value["shots"]
    unresolved = value["unresolved_requirements"]
    if not isinstance(scenes, list) or not scenes or len(scenes) > 30 or not isinstance(shots, list) or not 1 <= len(shots) <= 30:
        raise DomainError("Draft cần 1–30 scene/shot có cấu trúc.")
    if not isinstance(unresolved, list) or any(not isinstance(item, str) or not item.strip() for item in unresolved):
        raise DomainError("unresolved_requirements phải là danh sách text.")
    scene_ids = set()
    clean_scenes = []
    for index, scene in enumerate(scenes):
        _only_fields(scene, {"scene_id", "heading", "action_summary"}, {"scene_id", "heading", "action_summary"}, f"scenes[{index}]")
        if any(not isinstance(scene[key], str) or not scene[key].strip() for key in ("scene_id", "heading", "action_summary")) or scene["scene_id"] in scene_ids:
            raise DomainError("Scene ID/heading/action_summary thiếu hoặc trùng.")
        scene_ids.add(scene["scene_id"])
        clean_scenes.append(scene)
    seen_codes = set()
    siblings = []
    clean_shots = []
    issues = []
    for index, shot in enumerate(shots):
        _only_fields(shot, {"scene_id", "manifest", "action_intent", "cinematography_intent", "dialogue_translation_drafts"},
                     {"scene_id", "manifest", "action_intent", "cinematography_intent", "dialogue_translation_drafts"}, f"shots[{index}]")
        if shot["scene_id"] not in scene_ids or any(not isinstance(shot[key], str) or not shot[key].strip() for key in ("action_intent", "cinematography_intent")):
            raise DomainError(f"shots[{index}]: scene/action/cinematography intent không hợp lệ.")
        manifest = _only_fields(shot["manifest"],
                                {"schema_version", "shot_id", "fps", "timeline", "route", "environment_id", "performances", "intentional_overlap_with", "camera_intent", "render_profile_id"},
                                {"schema_version", "shot_id", "fps", "timeline", "route", "environment_id", "performances"},
                                f"shots[{index}].manifest")
        code = manifest["shot_id"]
        if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_]+", code) or code in seen_codes:
            raise DomainError("Shot ID thiếu, trùng hoặc chứa ký tự không hợp lệ.")
        seen_codes.add(code)
        if manifest.get("fps") != fps:
            issues.append({"shot_id": code, "kind": "FPS", "message": "FPS khác screenplay/project."})
        translations = shot["dialogue_translation_drafts"]
        if not isinstance(translations, list):
            raise DomainError(f"{code}: dialogue_translation_drafts phải là danh sách.")
        for item in translations:
            _only_fields(item, {"performance_id", "source_text", "draft_text", "source_language", "target_language", "source_kind"},
                         {"performance_id", "source_text", "draft_text", "source_language", "target_language", "source_kind"}, "dialogue_translation_drafts[]")
            if any(not isinstance(item[key], str) for key in item):
                raise DomainError("Dialogue translation draft phải là text.")
            if item["source_kind"] not in ("LOCAL_FIXTURE_COPY", "MODEL_DRAFT"):
                raise DomainError("Nguồn translation draft không hợp lệ.")
            if item["performance_id"] not in {p.get("performance_id") for p in manifest.get("performances", []) if isinstance(p, dict)}:
                issues.append({"shot_id": code, "kind": "TRANSLATION", "message": "Translation draft không khớp performance ID."})
            if item["source_language"] != item["target_language"] and not item["draft_text"].strip():
                issues.append({"shot_id": code, "kind": "TRANSLATION", "message": "Thiếu bản dịch thoại để duyệt."})
        known_ids = set(registry)
        referenced = {manifest.get("environment_id")}
        if manifest.get("render_profile_id"):
            referenced.add(manifest["render_profile_id"])
        for p in manifest.get("performances", []):
            if not isinstance(p, dict):
                raise DomainError(f"{code}: performance không hợp lệ.")
            referenced.update(p.get(k) for k in ("character_id", "anchor_id", "animation_id", "dialogue_id", "voice_profile_id", "retarget_profile_id", "paired_asset_id") if p.get(k))
        unknown = sorted(str(item) for item in referenced if item not in known_ids)
        if unknown:
            issues.extend({"shot_id": code, "kind": "UNRESOLVED_ID", "message": f"ID chưa có trong registry: {item}"} for item in unknown)
        else:
            try:
                validate_manifest(manifest, registry, siblings)
            except DomainError as exc:
                issues.append({"shot_id": code, "kind": "COMPATIBILITY_OR_TIMING", "message": str(exc)})
        if code in existing_codes:
            issues.append({"shot_id": code, "kind": "EXISTING_SHOT", "message": "Shot đã có; cần xem diff và expected revision trước apply."})
        siblings.append(manifest)
        clean_shots.append({**shot, "validation": {"allowed": not any(i["shot_id"] == code and i["kind"] != "EXISTING_SHOT" for i in issues),
                                                       "issues": [i for i in issues if i["shot_id"] == code]}})
    total_frames = sum(s["manifest"]["timeline"]["out_exclusive"] - s["manifest"]["timeline"]["in"] for s in clean_shots
                       if isinstance(s["manifest"].get("timeline"), dict) and type(s["manifest"]["timeline"].get("in")) is int and type(s["manifest"]["timeline"].get("out_exclusive")) is int)
    expected_frames = round(estimated_seconds * fps["num"] / fps["den"])
    if abs(total_frames - expected_frames) > max(1, expected_frames // 10):
        issues.append({"shot_id": "*", "kind": "DURATION", "message": "Tổng thời lượng draft lệch quá 10% so với screenplay estimate."})
    return {"schema_version": SCHEMA_VERSION, "screenplay_id": screenplay_id,
            "scenes": clean_scenes, "shots": clean_shots,
            "unresolved_requirements": list(dict.fromkeys(unresolved + [i["message"] for i in issues if i["kind"] == "UNRESOLVED_ID"])),
            "issues": issues, "valid_for_apply": not unresolved and not any(i["kind"] != "EXISTING_SHOT" for i in issues),
            "total_duration_frames": total_frames, "expected_duration_frames": expected_frames}
