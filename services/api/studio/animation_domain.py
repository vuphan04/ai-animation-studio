"""Pure rules for workflow V4.5 step 08-b1..b3: split a shot into character actions and match them to animations.

A match is a suggestion with a confidence; only an assignment the operator approved is ever sent to Unreal.
"""

from __future__ import annotations

import re

from .domain import DomainError, digest
from .series_domain import similarity


ANIM_PREFIX = re.compile(r"^(?:AN|ANIM|A|AS|AM|MM|MF|BS|RTG)_", re.IGNORECASE)
MISSING_BELOW = 0.45
LOW_BELOW = 0.7
# How much a match is worth by what it takes to play the clip on the character's skeleton.
SKELETON_FACTOR = {"DIRECT": 1.0, "EXISTING": 0.95, "NEEDS_CREATE": 0.8, "UNDECIDED": 0.5}
DEFAULT_TAG = "idle"
MAX_CLIPS = 4


def layout(shot: dict, character: dict) -> list[dict]:
    """One slot per motion tag of the character. Action cues from C4 are the cut points when they fit the
    number of tags; otherwise the shot is split evenly. A character without tags just stands (idle)."""
    tags = character["motion_tags"] or [DEFAULT_TAG]
    length = shot["length_frames"]
    cuts = sorted({cue["frame"] for cue in shot["cues"] if 0 < cue["frame"] < length})
    bounds = [0, *cuts, length] if len(cuts) == len(tags) - 1 else [round(i * length / len(tags)) for i in range(len(tags) + 1)]
    slots = []
    for position, tag in enumerate(tags):
        if bounds[position + 1] > bounds[position]:
            slots.append({"slot_id": f"{shot['shot_id']}_{character['entity_key']}_{position + 1:02d}", "shot_id": shot["shot_id"],
                          "character_key": character["entity_key"], "position": position, "tag": tag,
                          "start_frame": bounds[position], "end_frame": bounds[position + 1]})
    return slots


def basis(slot: dict, target_skeleton: str | None) -> str:
    return digest({key: slot[key] for key in ("tag", "start_frame", "end_frame", "character_key")} | {"skeleton": target_skeleton})


def retarget_mode(source_skeleton: str | None, target_skeleton: str | None, character_key: str, pairs: list[dict]) -> dict:
    """How a clip gets onto the character: directly, through an approved retargeter, or not decided yet."""
    if source_skeleton and source_skeleton == target_skeleton:
        return {"mode": "DIRECT", "retargeter_path": None}
    pair = next((p for p in pairs if p["source_skeleton"] == (source_skeleton or "") and p["target_entity_key"] == character_key), None)
    if not pair:
        return {"mode": "UNDECIDED", "retargeter_path": None}
    return {"mode": pair["mode"], "retargeter_path": pair["retargeter_path"]}


def match(tag: str, candidates: list[dict], target_skeleton: str | None, character_key: str, pairs: list[dict], library_tags: dict) -> dict | None:
    """Best animation for a tag: name and library tags first, then how directly its skeleton fits."""
    best = None
    for asset in candidates:
        name = 0.95 if tag in library_tags.get(asset["object_path"], ()) else similarity([tag], ANIM_PREFIX.sub("", asset["asset_name"]))
        mode = retarget_mode(asset["skeleton_path"], target_skeleton, character_key, pairs)["mode"]
        confidence = round(name * SKELETON_FACTOR[mode], 2)
        if best is None or (confidence, asset["object_path"]) > (best["confidence"], best["asset_path"]):
            best = {"asset_path": asset["object_path"], "confidence": confidence}
    return best if best and best["confidence"] >= MISSING_BELOW else None


def clean_clips(clips: object, slot_frames: int) -> list[dict]:
    """08-b3: one clip, or several played one after another with a blend between them."""
    if not isinstance(clips, list) or not 1 <= len(clips) <= MAX_CLIPS:
        raise DomainError(f"Mỗi hành động cần từ 1 đến {MAX_CLIPS} animation.")
    clean = []
    for position, clip in enumerate(clips):
        if not isinstance(clip, dict) or set(clip) != {"asset_path", "blend_frames"} or not isinstance(clip["asset_path"], str) or type(clip["blend_frames"]) is not int:
            raise DomainError("Mỗi animation cần đường dẫn asset và số frame blend.")
        if not 0 <= clip["blend_frames"] <= slot_frames or (position == 0 and clip["blend_frames"]):
            raise DomainError("Frame blend phải nằm trong độ dài hành động; animation đầu tiên không blend vào.")
        clean.append({"asset_path": clip["asset_path"], "blend_frames": clip["blend_frames"]})
    return clean
