"""Pure rules for an episode breakdown (workflow V4.5 step 03): scenes, shots, stable IDs and library matching.

A breakdown is content only. It names characters and environments; it never carries asset paths, routes or approvals.
"""

from __future__ import annotations

import re

from .domain import DomainError, digest
from .series_domain import SCENE_HEADING, _clean_name, fold, _forbidden_keys
from .style_domain import banned_hits, build_prompt


SCHEMA = "breakdown.1"
SHOT_SIZES = ("EXTREME_WIDE", "WIDE", "MEDIUM", "CLOSE_UP", "EXTREME_CLOSE_UP", "OVER_SHOULDER", "TWO_SHOT", "INSERT")
CONTENT_FLAGS = ("VIOLENCE", "BLOOD")
TAG = re.compile(r"^[a-z0-9][a-z0-9 _-]{0,29}$")
LIMITS = {"heading": 200, "summary": 1000, "name": 80, "description": 1000, "camera_note": 300, "prompt_en": 1500,
          "action": 300, "text": 2000}
LABELS = {"heading": "tiêu đề scene", "summary": "tóm tắt scene", "name": "tên nhân vật hoặc bối cảnh", "description": "mô tả shot",
          "camera_note": "ghi chú máy quay", "prompt_en": "prompt tiếng Anh", "action": "hành động", "text": "lời thoại"}


def entity_index(entities: list[dict]) -> dict:
    """Names and aliases of the active library entities, folded for matching."""
    index = {"character": {}, "environment": {}}
    for entity in entities:
        index[entity["kind"]][entity["entity_key"]] = {fold(entity["display_name"]), *(fold(a) for a in entity["aliases"])}
    return index


def resolve(index: dict, kind: str, name: str, key: str | None = None) -> str | None:
    """An explicit key wins while that entity exists; otherwise the name must match a library name or alias."""
    if key and key in index[kind]:
        return key
    folded = fold(name)
    return next((entity_key for entity_key, names in index[kind].items() if folded and folded in names), None)


def _text(value: object, field: str, *, required: bool, lenient: bool) -> str:
    if not isinstance(value, str):
        if lenient and value is None:
            value = ""
        else:
            raise DomainError(f"{LABELS[field].capitalize()} phải là chữ.")
    text = value.strip()
    limit = LIMITS[field]
    if len(text) > limit:
        if not lenient:
            raise DomainError(f"{LABELS[field].capitalize()} tối đa {limit} ký tự.")
        text = text[:limit]
    if required and not text:
        raise DomainError(f"Thiếu {LABELS[field]}.")
    return text


def _tags(value: object, lenient: bool) -> list[str]:
    if not isinstance(value, list):
        if lenient:
            return []
        raise DomainError("Nhãn motion/VFX phải là danh sách.")
    result = []
    for item in value:
        tag = item.strip().lower() if isinstance(item, str) else ""
        if TAG.fullmatch(tag):
            if tag not in result:
                result.append(tag)
        elif not lenient:
            raise DomainError("Nhãn motion/VFX chỉ gồm chữ thường tiếng Anh, số, dấu cách hoặc gạch, tối đa 30 ký tự.")
    if len(result) > 10 and not lenient:
        raise DomainError("Tối đa 10 nhãn cho mỗi mục.")
    return result[:10]


def clean_shot(raw: object, index: dict, *, lenient: bool) -> dict:
    if not isinstance(raw, dict):
        raise DomainError("Mỗi shot phải là một đối tượng.")
    size = raw.get("shot_size")
    if size not in SHOT_SIZES:
        if not lenient:
            raise DomainError("Cỡ cảnh không hợp lệ.")
        size = "MEDIUM"
    duration = raw.get("duration_seconds")
    if type(duration) not in (int, float) or not 0.5 <= duration <= 60:
        if not lenient or type(duration) not in (int, float):
            raise DomainError("Thời lượng dự kiến của shot phải từ 0,5 đến 60 giây.")
        duration = min(max(duration, 0.5), 60)
    flags = raw.get("content_flags", [])
    if not isinstance(flags, list) or (not lenient and any(f not in CONTENT_FLAGS for f in flags)):
        raise DomainError("Nhãn nội dung không hợp lệ.")
    characters, seen = [], set()
    for item in raw.get("characters", []) if isinstance(raw.get("characters", []), list) else []:
        if not isinstance(item, dict):
            raise DomainError("Nhân vật trong shot không hợp lệ.")
        name = _text(item.get("name"), "name", required=not lenient, lenient=lenient)
        if not name:
            continue
        if fold(name) in seen:
            if lenient:
                continue
            raise DomainError(f"Nhân vật {name} bị lặp trong cùng một shot.")
        seen.add(fold(name))
        characters.append({"name": name, "entity_key": resolve(index, "character", name, item.get("entity_key") or None),
                           "action": _text(item.get("action", ""), "action", required=False, lenient=lenient),
                           "motion_tags": _tags(item.get("motion_tags", []), lenient)})
    dialogue = []
    for item in raw.get("dialogue", []) if isinstance(raw.get("dialogue", []), list) else []:
        if not isinstance(item, dict):
            raise DomainError("Lời thoại không hợp lệ.")
        speaker = item.get("character") if isinstance(item.get("character"), dict) else {"name": item.get("character_name"), "entity_key": item.get("entity_key")}
        text = _text(item.get("text"), "text", required=not lenient, lenient=lenient)
        if not text:
            continue
        name = _text(speaker.get("name"), "name", required=not lenient, lenient=lenient)
        dialogue.append({"line_key": item.get("line_key") if isinstance(item.get("line_key"), str) else None,
                         "character": {"name": name, "entity_key": resolve(index, "character", name, speaker.get("entity_key") or None)},
                         "text": text, "text_en": _text(item.get("text_en", ""), "text", required=False, lenient=lenient)})
    if type(raw.get("face_visible")) is not bool and not lenient:
        raise DomainError("Cần cho biết shot có lộ mặt nhân vật hay không.")
    return {"shot_key": raw.get("shot_key") if isinstance(raw.get("shot_key"), str) else None,
            "description": _text(raw.get("description"), "description", required=True, lenient=lenient),
            "description_en": _text(raw.get("description_en", ""), "description", required=False, lenient=lenient),
            "shot_size": size, "camera_note": _text(raw.get("camera_note", ""), "camera_note", required=False, lenient=lenient),
            "duration_seconds": round(float(duration), 2), "face_visible": bool(raw.get("face_visible")),
            "content_flags": [f for f in CONTENT_FLAGS if f in flags], "vfx_tags": _tags(raw.get("vfx_tags", []), lenient),
            "prompt_en": _text(raw.get("prompt_en", ""), "prompt_en", required=False, lenient=lenient),
            "characters": characters, "dialogue": dialogue}


def clean_scene(raw: object, index: dict, *, lenient: bool = False) -> dict:
    """Validate one scene from the operator (strict) or from a model draft (lenient: clamp and drop, never invent).

    Only the known content fields are kept, so nothing else in the payload can reach the stored breakdown.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("shots"), list) or not 1 <= len(raw["shots"]) <= 80:
        raise DomainError("Mỗi scene cần từ 1 đến 80 shot.")
    environment = raw.get("environment") if isinstance(raw.get("environment"), dict) else {"name": raw.get("environment_name"), "entity_key": raw.get("environment_key")}
    name = _text(environment.get("name"), "name", required=not lenient, lenient=lenient)
    return {"scene_key": raw.get("scene_key") if isinstance(raw.get("scene_key"), str) else None,
            "heading": _text(raw.get("heading"), "heading", required=True, lenient=lenient),
            "summary": _text(raw.get("summary", ""), "summary", required=False, lenient=lenient),
            "environment": {"name": name, "entity_key": resolve(index, "environment", name, environment.get("entity_key") or None)},
            "shots": [clean_shot(shot, index, lenient=lenient) for shot in raw["shots"]]}


def clean_draft(value: object, index: dict) -> list[dict]:
    # A model draft that tries to carry approvals, asset paths or commands is rejected outright.
    _forbidden_keys(value, "breakdown")
    if not isinstance(value, dict) or not isinstance(value.get("scenes"), list) or not 1 <= len(value["scenes"]) <= 120:
        raise DomainError("Bản phân tích cần từ 1 đến 120 scene.")
    return [clean_scene(scene, index, lenient=True) for scene in value["scenes"]]


def assign_keys(content: dict) -> dict:
    """Give new scenes, shots and lines the next free number. Numbers are never reused within an episode."""
    for scene in content["scenes"]:
        if not scene.get("scene_key"):
            scene["scene_key"] = f"SC{content['next_scene'] * 10:03d}"
            content["next_scene"] += 1
        scene.setdefault("next_shot", 1)
        scene.setdefault("next_line", 1)
        for shot in scene["shots"]:
            if not shot.get("shot_key"):
                shot["shot_key"] = f"SH{scene['next_shot'] * 10:03d}"
                scene["next_shot"] += 1
            for line in shot["dialogue"]:
                if not line.get("line_key"):
                    line["line_key"] = f"L{scene['next_line']:03d}"
                    scene["next_line"] += 1
    return content


def scene_hash(scene: dict) -> str:
    return digest({key: value for key, value in scene.items() if key not in ("next_shot", "next_line")})


def _by_heading(scenes: list[dict]) -> dict:
    seen: dict[str, int] = {}
    result = {}
    for scene in scenes:
        folded = fold(scene["heading"])
        result[(folded, seen.get(folded, 0))] = scene
        seen[folded] = seen.get(folded, 0) + 1
    return result


def merge_draft(prior: dict | None, drafted: list[dict], protected: set[str]) -> dict:
    """Fold a model draft into the current breakdown without touching approved scenes or dropping existing ones."""
    existing = prior["scenes"] if prior else []
    known = _by_heading(existing)
    result, used = [], set()
    for match, draft in _by_heading(drafted).items():
        old = known.get(match)
        if old is None:
            scene = dict(draft, scene_key=None)
        elif old["scene_key"] in protected:
            scene = old
        else:
            scene = dict(draft, scene_key=old["scene_key"], next_shot=old["next_shot"], next_line=old["next_line"])
            for position, shot in enumerate(scene["shots"]):
                if position < len(old["shots"]):
                    shot["shot_key"] = old["shots"][position]["shot_key"]
                    for line_position, line in enumerate(shot["dialogue"]):
                        if line_position < len(old["shots"][position]["dialogue"]):
                            line["line_key"] = old["shots"][position]["dialogue"][line_position]["line_key"]
        if old is not None:
            used.add(old["scene_key"])
        result.append(scene)
    for position, old in enumerate(existing):
        if old["scene_key"] in used:
            continue
        placed = {scene.get("scene_key") for scene in result}
        anchor = next((existing[i]["scene_key"] for i in range(position - 1, -1, -1) if existing[i]["scene_key"] in placed), None)
        result.insert(next((i + 1 for i, scene in enumerate(result) if scene.get("scene_key") == anchor), 0) if anchor else 0, old)
    return assign_keys({"schema_version": SCHEMA, "next_scene": prior["next_scene"] if prior else 1, "scenes": result})


def apply_scene_edit(content: dict, edited: dict) -> dict:
    """Replace one scene with an operator edit, keeping its counters and rejecting keys the scene never had."""
    stored = next((scene for scene in content["scenes"] if scene["scene_key"] == edited["scene_key"]), None)
    if stored is None:
        raise DomainError("Scene không còn trong breakdown hiện tại; tải lại.", 409)
    shot_keys = {shot["shot_key"] for shot in stored["shots"]}
    line_keys = {line["line_key"] for shot in stored["shots"] for line in shot["dialogue"]}
    seen_shots, seen_lines = set(), set()
    for shot in edited["shots"]:
        if shot["shot_key"] is not None and (shot["shot_key"] not in shot_keys or shot["shot_key"] in seen_shots):
            raise DomainError("Shot ID không thuộc scene này hoặc bị lặp.", 409)
        seen_shots.add(shot["shot_key"])
        for line in shot["dialogue"]:
            if line["line_key"] is not None and (line["line_key"] not in line_keys or line["line_key"] in seen_lines):
                raise DomainError("ID lời thoại không thuộc scene này hoặc bị lặp.", 409)
            seen_lines.add(line["line_key"])
    replacement = dict(edited, next_shot=stored["next_shot"], next_line=stored["next_line"])
    return assign_keys(dict(content, scenes=[replacement if scene["scene_key"] == edited["scene_key"] else scene for scene in content["scenes"]]))


def scene_review(scene: dict, index: dict, banned: list[str], style_phrases: list[str], episode_code: str) -> dict:
    """What the operator sees for one scene: effective library matches, styled prompts, blockers and warnings."""
    issues, warnings, shots = [], [], []
    environment_key = resolve(index, "environment", scene["environment"]["name"], scene["environment"]["entity_key"])
    if not scene["environment"]["name"]:
        issues.append("Scene chưa có bối cảnh.")
    elif not environment_key:
        warnings.append(f"Bối cảnh «{scene['environment']['name']}» chưa có trong thư viện; xử lý ở bước 04.")
    untranslated = 0
    for shot in scene["shots"]:
        characters = [dict(c, effective_key=resolve(index, "character", c["name"], c["entity_key"])) for c in shot["characters"]]
        dialogue = [dict(line, effective_key=resolve(index, "character", line["character"]["name"], line["character"]["entity_key"])) for line in shot["dialogue"]]
        for name in sorted({c["name"] for c in characters if not c["effective_key"]} | {l["character"]["name"] for l in dialogue if not l["effective_key"] and l["character"]["name"]}):
            message = f"Nhân vật «{name}» chưa có trong thư viện; xử lý ở bước 04."
            if message not in warnings:
                warnings.append(message)
        if any(not line["character"]["name"] for line in dialogue):
            issues.append(f"{shot['shot_key']}: có lời thoại chưa ghi người nói.")
        hits = banned_hits(shot["prompt_en"], banned)
        if hits:
            issues.append(f"{shot['shot_key']}: prompt chứa từ cấm của Style bible: {', '.join(hits)}.")
        if not shot["prompt_en"] or not shot["description_en"] or any(not line["text_en"] for line in shot["dialogue"]):
            untranslated += 1
        shots.append(dict(shot, characters=characters, dialogue=dialogue,
                          shot_id=f"{episode_code}_{scene['scene_key']}_{shot['shot_key']}",
                          styled_prompt=build_prompt(shot["prompt_en"], style_phrases) if shot["prompt_en"] else ""))
    if untranslated:
        warnings.append(f"{untranslated} shot còn thiếu bản dịch hoặc prompt tiếng Anh.")
    return {**{k: v for k, v in scene.items() if k not in ("next_shot", "next_line")}, "shots": shots,
            "environment": dict(scene["environment"], effective_key=environment_key), "scene_hash": scene_hash(scene),
            "duration_seconds": round(sum(shot["duration_seconds"] for shot in scene["shots"]), 2),
            "issues": issues, "warnings": warnings}


def usage(content: dict, index: dict, episode_code: str) -> dict:
    """Characters and environments the episode uses, grouped by library entity or, when unmatched, by name."""
    result = {"character": {}, "environment": {}}

    def note(kind: str, name: str, key: str | None, scene_key: str, shot_id: str | None) -> None:
        effective = resolve(index, kind, name, key)
        item = result[kind].setdefault(effective or "name:" + fold(name), {"entity_key": effective, "names": [], "scenes": [], "shots": []})
        if name not in item["names"]:
            item["names"].append(name)
        if scene_key not in item["scenes"]:
            item["scenes"].append(scene_key)
        if shot_id and shot_id not in item["shots"]:
            item["shots"].append(shot_id)

    for scene in content["scenes"]:
        if scene["environment"]["name"]:
            note("environment", scene["environment"]["name"], scene["environment"]["entity_key"], scene["scene_key"], None)
        for shot in scene["shots"]:
            shot_id = f"{episode_code}_{scene['scene_key']}_{shot['shot_key']}"
            for character in shot["characters"]:
                note("character", character["name"], character["entity_key"], scene["scene_key"], shot_id)
            for line in shot["dialogue"]:
                note("character", line["character"]["name"], line["character"]["entity_key"], scene["scene_key"], shot_id)
    return result


def tag_usage(content: dict, episode_code: str) -> tuple[dict, dict]:
    motion: dict[str, list[str]] = {}
    vfx: dict[str, list[str]] = {}
    for scene in content["scenes"]:
        for shot in scene["shots"]:
            shot_id = f"{episode_code}_{scene['scene_key']}_{shot['shot_key']}"
            for tag in shot["vfx_tags"]:
                vfx.setdefault(tag, []).append(shot_id)
            for character in shot["characters"]:
                for tag in character["motion_tags"]:
                    if shot_id not in motion.setdefault(tag, []):
                        motion[tag].append(shot_id)
    return motion, vfx


def fixture_draft(text: str) -> dict:
    """Rule-based split for control-flow tests: one shot per line. It does not translate or write prompts."""
    scenes: list[dict] = []
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if SCENE_HEADING.match(stripped) or current is None:
            heading = stripped if SCENE_HEADING.match(stripped) else "CẢNH 1"
            place = _clean_name(re.sub(SCENE_HEADING, "", heading).split(" - ")[0])
            current = {"heading": heading[:200], "summary": "", "environment_name": place, "environment_key": "", "shots": []}
            scenes.append(current)
            if SCENE_HEADING.match(stripped):
                continue
        speaker = re.match(r"^([^:]{1,40}):\s+(\S.*)$", stripped)
        name = _clean_name(speaker.group(1)) if speaker else ""
        if speaker and name and len(name.split()) <= 4:
            name = name.title() if name.isupper() else name
            words = len(speaker.group(2).split())
            current["shots"].append({"description": f"{name} nói.", "description_en": "", "shot_size": "MEDIUM", "camera_note": "",
                                     "duration_seconds": round(min(max(1.5 + words * 0.4, 2.0), 12.0), 1), "face_visible": True,
                                     "content_flags": [], "vfx_tags": [], "prompt_en": "",
                                     "characters": [{"name": name, "entity_key": "", "action": "nói", "motion_tags": ["talk"]}],
                                     "dialogue": [{"character_name": name, "entity_key": "", "text": speaker.group(2).strip(), "text_en": ""}]})
        else:
            if not current["summary"]:
                current["summary"] = stripped[:1000]
            current["shots"].append({"description": stripped[:1000], "description_en": "", "shot_size": "WIDE", "camera_note": "",
                                     "duration_seconds": 4.0, "face_visible": False, "content_flags": [], "vfx_tags": [],
                                     "prompt_en": "", "characters": [], "dialogue": []})
    for scene in scenes:
        if not scene["shots"]:
            scene["shots"].append({"description": scene["heading"], "description_en": "", "shot_size": "WIDE", "camera_note": "",
                                   "duration_seconds": 4.0, "face_visible": False, "content_flags": [], "vfx_tags": [],
                                   "prompt_en": "", "characters": [], "dialogue": []})
    return {"scenes": scenes}
