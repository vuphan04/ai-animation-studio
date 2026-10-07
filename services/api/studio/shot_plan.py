"""What zone B builds from: every shot of an episode with its locked route (C3) and approved timing (C4).

Nothing is stored here. The plan is recomputed from the approved breakdown, library, Style bible, routes and
timing each time, so a shot can never be built from something that is no longer approved.
"""

from __future__ import annotations

import json

from .breakdown_domain import entity_index, resolve
from .domain import digest
from .timing_domain import to_seconds


def load(routes, timing, conn, episode: dict) -> dict:
    state = timing._state(conn, episode)
    plan = state["_inputs"]["plan"]
    blockers = []
    if not (plan["c3"] and plan["c3"]["current"]):
        blockers.append("C3: phương thức từng shot chưa được chốt.")
    if not (state["c4"] and state["c4"]["current"]):
        blockers.append("C4: thoại và timing chưa được duyệt.")
    base = {"episode": episode, "blockers": blockers, "ready": not blockers, "shots": [], "by_id": {}, "fps": state["fps"]}
    if blockers:
        return base
    stored, context = state["_stored"], state["_inputs"]["context"]
    fps = stored["fps"]
    entities = plan["entities"]
    index = entity_index(list(entities.values()))
    scan = conn.execute("SELECT id FROM ue_asset_scans WHERE project_id=? AND state='COMPLETED' ORDER BY created_at DESC LIMIT 1", (episode["project_id"],)).fetchone()
    bible = conn.execute("SELECT version,snapshot_json FROM style_bible_versions WHERE project_id=? ORDER BY version DESC LIMIT 1", (episode["project_id"],)).fetchone()
    style = json.loads(bible["snapshot_json"])
    library = conn.execute("SELECT version,snapshot_json FROM series_library_versions WHERE project_id=? ORDER BY version DESC LIMIT 1", (episode["project_id"],)).fetchone()

    def skeleton(mesh_path: str | None) -> str | None:
        row = conn.execute("SELECT skeleton_path FROM ue_scan_assets WHERE scan_id=? AND object_path=?", (scan["id"], mesh_path)).fetchone() if scan and mesh_path else None
        return row["skeleton_path"] if row else None

    def member(kind: str, name: str, key: str | None) -> dict:
        found = resolve(index, kind, name, key)
        entity = entities.get(found) if found else None
        mapping = entity["mapping"] if entity and entity["mapping"] and entity["mapping"]["status"] == "MAPPED" else None
        return {"entity_key": found, "display_name": entity["display_name"] if entity else name, "mapped": bool(mapping),
                "asset_path": mapping["asset_path"] if mapping else None,
                "body_mesh_path": mapping["body_mesh_path"] if mapping else None, "face_mesh_path": mapping["face_mesh_path"] if mapping else None}

    decided = {shot["shot_id"]: shot for scene in plan["scenes"] for shot in scene["shots"]}
    timed = {shot["shot_id"]: shot for shot in stored["shots"]}
    content = json.loads(context["revision"]["content_json"])
    shots, cursor = [], 0
    for scene in content["scenes"]:
        environment = member("environment", scene["environment"]["name"], scene["environment"]["entity_key"])
        for source in scene["shots"]:
            shot_id = f"{episode['code']}_{scene['scene_key']}_{source['shot_key']}"
            route, time = decided[shot_id], timed[shot_id]
            characters, seen = [], set()
            for character in source["characters"]:
                item = member("character", character["name"], character["entity_key"])
                seen.add(item["entity_key"])
                characters.append({**item, "action": character["action"], "motion_tags": character["motion_tags"], "skeleton_path": skeleton(item["body_mesh_path"])})
            lines, local = [], 0
            for line in time["lines"]:
                local += line["pause_before_frames"]
                spoken = context["lines"][line["line_id"]]
                take = spoken["selected"]
                lines.append({"line_id": line["line_id"], "speaker_key": spoken["speaker_key"], "text": spoken["spoken_text"], "take_id": take["id"],
                              "original_text": spoken["text"], "text_en": spoken["text_en"],
                              "relative_path": take["relative_path"], "sha256": take["sha256"], "start_frame": local, "voice_frames": line["voice_frames"]})
                local += line["voice_frames"]
                if spoken["speaker_key"] not in seen:
                    # Someone who only speaks in the shot still stands in it.
                    seen.add(spoken["speaker_key"])
                    item = member("character", spoken["speaker"], spoken["speaker_key"])
                    characters.append({**item, "action": "", "motion_tags": [], "skeleton_path": skeleton(item["body_mesh_path"])})
            shot = {"shot_id": shot_id, "scene_key": scene["scene_key"], "scene_heading": scene["heading"], "route": route["route"], "video_model": route["video_model"],
                    "start_frame": cursor, "length_frames": time["length_frames"], "seconds": to_seconds(time["length_frames"], fps),
                    "description": source["description"], "description_en": source["description_en"], "shot_size": source["shot_size"],
                    "camera_note": source["camera_note"], "prompt_en": source["prompt_en"], "face_visible": source["face_visible"],
                    "content_flags": source["content_flags"], "vfx_tags": source["vfx_tags"], "environment": environment,
                    "characters": characters, "lines": lines, "cues": time["cues"], "kimodo_tags": route["kimodo_tags"]}
            shot["shot_hash"] = digest(shot)
            cursor += time["length_frames"]
            shots.append(shot)
    snapshot = json.loads(library["snapshot_json"])
    return {**base, "fps": fps, "shots": shots, "by_id": {shot["shot_id"]: shot for shot in shots}, "total_frames": cursor,
            "timing_hash": state["revision"]["content_hash"], "language": context["language"]["value"], "prices": plan["prices"], "scan_id": scan["id"] if scan else None,
            "library_version": library["version"], "animation_folders": snapshot["animation_folders"], "retarget_pairs": snapshot["retarget_pairs"],
            "style": {"version": bible["version"], "phrases": style["style_phrases"], "banned": style["banned_words"],
                      "entities": {entity["entity_key"]: entity for entity in style["entities"]}}}
