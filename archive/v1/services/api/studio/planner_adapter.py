"""Planner adapter contract and deterministic local control-flow fixture."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass

from .domain import digest
from .p5_domain import SCHEMA_VERSION


@dataclass(frozen=True)
class PlannerResult:
    raw_json: str
    provider_request_id: str
    actual_cost_microusd: int
    resolved_model: str | None = None


class PlannerProvider:
    kind = "ABSTRACT"
    paid = False
    configured = False

    def estimate_microusd(self, request: dict) -> int:
        raise NotImplementedError

    def generate(self, request: dict, *, previous_error: str | None, attempt: int) -> PlannerResult:
        raise NotImplementedError


def screenplay_scenes(text: str) -> list[dict]:
    scenes: list[dict] = []
    current = {"heading": "CẢNH 1", "lines": []}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^(?:INT\.|EXT\.|CẢNH\s+\d+|SCENE\s+\d+)", stripped, re.IGNORECASE):
            if current["lines"]:
                scenes.append(current)
            current = {"heading": stripped[:120], "lines": []}
        else:
            current["lines"].append(stripped)
    if current["lines"] or not scenes:
        scenes.append(current)
    return scenes[:12]


class LocalFixturePlanner(PlannerProvider):
    kind = "LOCAL_PLANNER_FIXTURE"
    paid = False
    configured = True

    def estimate_microusd(self, request: dict) -> int:
        return 0

    def generate(self, request: dict, *, previous_error: str | None, attempt: int) -> PlannerResult:
        registry = request["registry_context"]
        by_kind: dict[str, list[dict]] = {}
        for item in registry:
            by_kind.setdefault(item["kind"], []).append(item)
        fps = request["fps"]
        total = math.ceil(request["estimated_seconds"] * fps["num"] / fps["den"])
        count = max(4, min(6, round(request["estimated_seconds"] / 18)))
        length, extra = divmod(total, count)
        scenes_in = screenplay_scenes(request["screenplay_text"])
        scenes = []
        for index, scene in enumerate(scenes_in):
            scenes.append({"scene_id": f"SCENE_{index+1:02d}", "heading": scene["heading"],
                           "action_summary": " ".join(scene["lines"])[:400] or "Không có hành động mô tả."})
        environment = next(iter(by_kind.get("environment", [])), None)
        anchor = next(iter(by_kind.get("anchor", [])), None)
        characters = by_kind.get("character", [])
        animations = by_kind.get("animation", [])
        dialogues = by_kind.get("dialogue", [])
        profiles = by_kind.get("voice_profile", [])
        unresolved = []
        if not environment:
            unresolved.append("Cần environment ID trong registry.")
        if characters and not anchor:
            unresolved.append("Cần anchor ID trong registry.")
        prefix = re.sub(r"[^A-Za-z0-9_]", "", request["episode_name"].upper()) or "EP"
        existing = set(request["existing_shot_codes"])
        preferred = request.get("preferred_shot_codes", [])
        cursor = request["start_frame"]
        shots = []
        used_dialogue = set()
        for index in range(count):
            duration = length + (1 if index < extra else 0)
            code_index = index + 1
            code = preferred[index] if index < len(preferred) else f"{prefix}_SQ900_SH{code_index*10:03d}"
            if index >= len(preferred):
                while code in existing:
                    code_index += count
                    code = f"{prefix}_SQ900_SH{code_index*10:03d}"
            existing.add(code)
            scene_id = scenes[min(index * len(scenes) // count, len(scenes) - 1)]["scene_id"]
            performances = []
            camera = None
            if characters and anchor:
                character = characters[index % len(characters)]
                animation = next((a for a in animations if character.get("skeleton_id") in a.get("compatible_skeletons", [])), None)
                if animation:
                    performance = {"performance_id": f"PERF_{index+1:02d}", "character_id": character["registry_id"],
                                   "anchor_id": anchor["registry_id"], "animation_id": animation["registry_id"]}
                    dialogue = next((d for d in dialogues if d["registry_id"] not in used_dialogue and
                                     d.get("character_id") == character["registry_id"] and d.get("text") and
                                     isinstance(d.get("duration_frames"), int) and d["duration_frames"] <= duration - 12), None)
                    profile = next((p for p in profiles if p.get("character_id") == character["registry_id"]), None)
                    if dialogue:
                        used_dialogue.add(dialogue["registry_id"])
                        performance.update({"dialogue_id": dialogue["registry_id"], "dialogue_start_local_frame": 12})
                        if profile:
                            performance["voice_profile_id"] = profile["registry_id"]
                        camera = {"mode": "static_dialogue", "subject": character["registry_id"], "shot_size": "MS"}
                    performances.append(performance)
                else:
                    unresolved.append(f"Thiếu animation tương thích cho {character['registry_id']}.")
            manifest = {"schema_version": "1.0", "shot_id": code, "fps": fps,
                        "timeline": {"in": cursor, "out_exclusive": cursor + duration},
                        "route": {"suggested": "UNREAL"},
                        "environment_id": environment["registry_id"] if environment else "UNRESOLVED_ENVIRONMENT",
                        "performances": performances}
            if camera:
                manifest["camera_intent"] = camera
            scene_text = next(s["action_summary"] for s in scenes if s["scene_id"] == scene_id)
            translations = []
            for p in performances:
                if p.get("dialogue_id"):
                    dialogue = next(d for d in dialogues if d["registry_id"] == p["dialogue_id"])
                    same = request["language"] == request["target_language"]
                    translations.append({"performance_id": p["performance_id"], "source_text": dialogue["text"],
                                         "draft_text": dialogue["text"] if same else "",
                                         "source_language": request["language"], "target_language": request["target_language"],
                                         "source_kind": "LOCAL_FIXTURE_COPY"})
                    if not same:
                        unresolved.append(f"{code}: cần bản dịch thoại thật cho {p['dialogue_id']}.")
            shots.append({"scene_id": scene_id, "manifest": manifest,
                          "action_intent": scene_text[:240], "cinematography_intent": "Góc tĩnh hội thoại; cần người dùng duyệt framing.",
                          "dialogue_translation_drafts": translations})
            cursor += duration
        draft = {"schema_version": SCHEMA_VERSION, "scenes": scenes, "shots": shots,
                 "unresolved_requirements": sorted(set(unresolved))}
        return PlannerResult(json.dumps(draft, ensure_ascii=False),
                             provider_request_id=f"fixture-{digest({'request': request['input_hash'], 'attempt': attempt})[:20]}",
                             actual_cost_microusd=0)
