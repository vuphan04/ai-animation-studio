"""P2 Unreal operation compiler. This module never imports or starts Unreal."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .domain import DomainError, digest


UE_PATH = re.compile(r"^/Game(?:/[A-Za-z0-9_]+)+$")
ACTOR_PATH = re.compile(r"^/Game/[A-Za-z0-9_/]+\.[A-Za-z0-9_]+(?::PersistentLevel\.[A-Za-z0-9_]+)?$")


def ue_path(value: object, field: str) -> str:
    if not isinstance(value, str) or not UE_PATH.fullmatch(value):
        raise DomainError(f"{field}: trusted /Game asset path required")
    return value


def actor_path(value: object, field: str) -> str:
    if not isinstance(value, str) or not ACTOR_PATH.fullmatch(value):
        raise DomainError(f"{field}: trusted level actor object path required")
    return value


def choose_motion(animations: list[dict], *, skeleton_id: str, tags: list[str], paired: bool = False) -> dict:
    """Deterministic library search; ties require a human's explicit registry ID."""
    wanted = set(tags)
    matches = [a for a in animations if a["kind"] == "animation"
               and set(a["metadata"].get("semantic_tags", [])) >= wanted
               and skeleton_id in a["metadata"].get("compatible_skeletons", [])
               and bool(a["metadata"].get("paired_asset_id")) == paired]
    matches.sort(key=lambda a: a["registry_id"])
    if len(matches) != 1:
        raise DomainError(f"motion query has {len(matches)} matches; select an exact animation registry ID")
    return matches[0]


def _asset(conn, pinned: dict, registry_id: str, kind: str) -> dict:
    ref = pinned.get(registry_id)
    if not ref:
        raise DomainError(f"registry ID {registry_id} is not pinned")
    row = conn.execute("SELECT * FROM asset_versions WHERE id=?", (ref["asset_version_id"],)).fetchone()
    if row is None or row["kind"] != kind or row["registry_id"] != registry_id or row["version"] != ref["version"]:
        raise DomainError(f"registry pin invalid for {registry_id}", 409)
    latest = conn.execute("SELECT MAX(version) FROM asset_versions WHERE project_id=? AND registry_id=?", (row["project_id"], registry_id)).fetchone()[0]
    if latest != row["version"]:
        raise DomainError(f"registry pin stale for {registry_id}; create and approve a new shot revision", 409)
    item = dict(row)
    item["metadata"] = json.loads(item.pop("metadata_json"))
    return item


def compile_episode(studio, project_id: str, episode_id: str, *, shot_codes: list[str] | None = None,
                    target_project: str | None = None) -> dict:
    """Compile 3–5 current, user approved shots into a fail-closed UE operation."""
    with studio.db.connect() as conn:
        episode = conn.execute("SELECT * FROM episodes WHERE id=? AND project_id=?", (episode_id, project_id)).fetchone()
        if episode is None:
            raise DomainError("episode not found", 404)
        if not re.fullmatch(r"EP\d{2}", episode["name"]):
            raise DomainError("episode name must be EP followed by two digits")
        rows = conn.execute("SELECT * FROM shots WHERE episode_id=? ORDER BY ordinal", (episode_id,)).fetchall()
        if shot_codes is not None:
            if len(set(shot_codes)) != len(shot_codes):
                raise DomainError("duplicate shot code")
            rows = [r for r in rows if r["code"] in shot_codes]
            if {r["code"] for r in rows} != set(shot_codes):
                raise DomainError("unknown shot code")
        if not 3 <= len(rows) <= 5:
            raise DomainError("P2 assembly requires 3–5 shots")
        plans = []
        world_path = None
        fps = None
        for row in rows:
            if not re.fullmatch(r"[A-Za-z0-9_]+", row["code"]):
                raise DomainError(f"{row['code']}: unsafe Unreal shot asset name", 409)
            revision = conn.execute("SELECT * FROM shot_revisions WHERE shot_id=? AND revision=?", (row["id"], row["current_revision"])).fetchone()
            manifest = json.loads(revision["manifest_json"])
            pinned = json.loads(revision["resolved_registry_json"])
            approval = conn.execute("SELECT decision_json FROM approvals WHERE project_id=? AND scope='plan' AND scope_id=? AND exact_version=?", (project_id, row["id"], row["current_revision"])).fetchone()
            if not approval or json.loads(approval[0])["approved_route"] != "UNREAL":
                raise DomainError(f"{row['code']}: exact UNREAL approval missing", 409)
            states = {r["subsystem"]: r for r in conn.execute("SELECT * FROM subsystem_states WHERE shot_id=?", (row["id"],))}
            for subsystem in ("body", "environment"):
                state = states[subsystem]
                if state["mode"] != "AUTO" or state["lock_state"] != "UNLOCKED":
                    raise DomainError(f"{row['code']}/{subsystem}: manual or locked", 409)
                if state["observed_fingerprint"] and state["observed_fingerprint"] != state["synced_fingerprint"]:
                    raise DomainError(f"{row['code']}/{subsystem}: external edit conflict", 409)
            env = _asset(conn, pinned, manifest["environment_id"], "environment")
            map_path = ue_path(env["metadata"].get("world_path"), "environment.world_path")
            if world_path is not None and world_path != map_path:
                raise DomainError("selected shots use different worlds")
            world_path = map_path
            if fps is not None and fps != manifest["fps"]:
                raise DomainError("selected shots use different FPS")
            fps = manifest["fps"]
            actors = []
            pairs: dict[str, list[dict]] = {}
            for performance in manifest["performances"]:
                character = _asset(conn, pinned, performance["character_id"], "character")
                anchor = _asset(conn, pinned, performance["anchor_id"], "anchor")
                animation = _asset(conn, pinned, performance["animation_id"], "animation")
                skeleton = character["metadata"].get("skeleton_id")
                retarget = None
                if skeleton not in animation["metadata"].get("compatible_skeletons", []):
                    profile = _asset(conn, pinned, performance["retarget_profile_id"], "retarget_profile")
                    meta = profile["metadata"]
                    source = animation["metadata"].get("source_skeleton_id")
                    if source not in animation["metadata"].get("compatible_skeletons", []) or meta.get("source_skeleton_id") != source or meta.get("target_skeleton_id") != skeleton:
                        raise DomainError(f"{row['code']}: retarget skeleton pair mismatch", 409)
                    source_path = ue_path(animation["metadata"].get("ue_path"), "animation.ue_path")
                    suffix = "_AAS_" + digest({"source_id": animation["id"], "target": character["id"],
                                                "profile_id": profile["id"]})[:12]
                    retarget = {"profile_id": profile["registry_id"], "profile_version": profile["version"],
                                "profile_path": ue_path(meta.get("ue_path"), "retarget_profile.ue_path"),
                                "source_mesh_path": ue_path(meta.get("source_mesh_path"), "retarget_profile.source_mesh_path"),
                                "target_mesh_path": ue_path(meta.get("target_mesh_path"), "retarget_profile.target_mesh_path"),
                                "suffix": suffix, "cache_path": source_path + suffix,
                                "cache_key": digest({"source_id": animation["id"], "target": character["id"], "profile_id": profile["id"]})}
                anim_meta = animation["metadata"]
                if anim_meta.get("paired_asset_id") and performance.get("paired_asset_id") != anim_meta["paired_asset_id"]:
                    raise DomainError(f"{row['code']}: paired clip requires matching paired asset", 409)
                actor = {"character_id": character["registry_id"], "character_version": character["version"],
                               "actor_path": actor_path(character["metadata"].get("actor_path"), "character.actor_path"),
                               "anchor_id": anchor["registry_id"], "anchor_version": anchor["version"],
                               "anchor_path": actor_path(anchor["metadata"].get("actor_path"), "anchor.actor_path"),
                               "animation_id": animation["registry_id"], "animation_version": animation["version"],
                               "animation_path": ue_path(anim_meta.get("ue_path"), "animation.ue_path"),
                               "semantic_tags": anim_meta.get("semantic_tags", []), "review": "NEEDS_REVIEW",
                               "retarget": retarget}
                actors.append(actor)
                if performance.get("paired_asset_id"):
                    pairs.setdefault(performance["paired_asset_id"], []).append(actor)
            for pair_id, members in pairs.items():
                pair = _asset(conn, pinned, pair_id, "paired_animation")
                meta = pair["metadata"]
                if len(members) != 2 or {m["character_id"]: m["animation_id"] for m in members} != meta.get("clips_by_character") or any(m["anchor_id"] != meta.get("anchor_id") for m in members) or type(meta.get("sync_point_local_frame")) is not int or not 0 <= meta["sync_point_local_frame"] < manifest["timeline"]["out_exclusive"] - manifest["timeline"]["in"]:
                    raise DomainError(f"{row['code']}: paired asset participants/anchor/sync mismatch", 409)
                for member in members:
                    member["paired_asset_id"] = pair_id
                    member["sync_point_local_frame"] = meta["sync_point_local_frame"]
            if len({actor["character_id"] for actor in actors}) != len(actors):
                raise DomainError(f"{row['code']}: duplicate character binding in shot", 409)
            plans.append({"shot_id": row["id"], "shot_code": row["code"], "shot_revision": row["current_revision"],
                          "input_hash": revision["input_hash"], "timeline": manifest["timeline"], "actors": actors,
                          "environment_id": env["registry_id"], "environment_version": env["version"],
                          "expected_states": {s: states[s]["state_revision"] for s in ("body", "environment")}})
        paths = [a["actor_path"] for p in plans for a in p["actors"]]
        if any(path == a["anchor_path"] for p in plans for a in p["actors"] for path in [a["actor_path"]]):
            raise DomainError("character and anchor cannot be the same actor")
        episode_key = episode["name"]
        payload = {"schema_version": "p2.operation.1", "project_id": project_id, "episode_id": episode_id,
                   "episode_code": episode_key, "master_path": f"/Game/AIAnimationStudio/{episode_key}/{episode_key}_MASTER",
                   "world_path": world_path, "fps": fps, "shots": plans}
        if target_project:
            payload["target_project"] = target_project
        semantic = {**payload, "shots": [{k: v for k, v in shot.items() if k != "expected_states"} for shot in plans]}
        payload["input_hash"] = digest(semantic)
        payload["operation_id"] = digest({"schema": payload["schema_version"], "project": project_id,
                                           "episode": episode_id, "input_hash": payload["input_hash"]})
        return payload


def validate_result(operation: dict, result: dict) -> None:
    if result.get("schema_version") != "p2.result.1" or result.get("adapter_kind") != "UNREAL_EDITOR_PYTHON":
        raise DomainError("Unreal result schema or adapter provenance mismatch", 409)
    if not isinstance(result.get("engine_version"), str) or not result["engine_version"].startswith("5.5."):
        raise DomainError("Unreal result UE version mismatch", 409)
    for key in ("operation_id", "input_hash", "master_path", "world_path"):
        if result.get(key) != operation.get(key):
            raise DomainError(f"Unreal result {key} mismatch", 409)
    if result.get("status") not in ("OBSERVED", "APPLIED") or not isinstance(result.get("output_fingerprint"), str):
        raise DomainError("Unreal output observation missing", 409)
    observation = result.get("observation")
    if not isinstance(observation, dict) or digest(observation) != result["output_fingerprint"]:
        raise DomainError("Unreal observation fingerprint mismatch", 409)
    folder = operation["master_path"].rsplit("/", 1)[0]
    expected_sections = sorted(({"path": f"{folder}/{s['shot_code']}", "in": s["timeline"]["in"],
                                 "out_exclusive": s["timeline"]["out_exclusive"]} for s in operation["shots"]),
                               key=lambda s: (s["in"], s["path"]))
    if observation.get("sections") != expected_sections:
        raise DomainError("Unreal managed shot sections differ from operation", 409)
    observed_shots = {s["shot_code"]: s for s in observation.get("shots", [])}
    if len(observed_shots) != len(operation["shots"]):
        raise DomainError("Unreal observed shot count mismatch", 409)
    for shot in operation["shots"]:
        actual = observed_shots.get(shot["shot_code"])
        expected_names = {"AAS_" + a["character_id"] for a in shot["actors"]}
        if actual is None or actual.get("sequence_path") != f"{folder}/{shot['shot_code']}" or {b["name"] for b in actual.get("bindings", [])} != expected_names or any(not b.get("guid") for b in actual.get("bindings", [])):
            raise DomainError("Unreal observed binding mismatch", 409)
        for actor in shot["actors"]:
            binding = next(b for b in actual["bindings"] if b["name"] == "AAS_" + actor["character_id"])
            if binding.get("resolved_actor_paths") != [actor["actor_path"]]:
                raise DomainError("Unreal binding did not resolve to the registered actor", 409)
            animation_path = actor["retarget"]["cache_path"] if actor.get("retarget") else actor["animation_path"]
            expected_anim = {"path": animation_path + "." + animation_path.rsplit("/", 1)[1],
                             "in": 0, "out_exclusive": shot["timeline"]["out_exclusive"] - shot["timeline"]["in"]}
            if binding.get("animations") != [expected_anim]:
                raise DomainError("Unreal observed animation section mismatch", 409)
    expected = {s["shot_code"]: s["timeline"] for s in operation["shots"]}
    observed = {s["shot_code"]: s["timeline"] for s in result.get("shots", [])}
    if expected != observed or len(result["shots"]) != len(operation["shots"]):
        raise DomainError("Unreal shot/frame observation mismatch", 409)
    for shot in result["shots"]:
        desired = next(s for s in operation["shots"] if s["shot_code"] == shot["shot_code"])
        if {a["character_id"] for a in shot.get("bindings", [])} != {a["character_id"] for a in desired["actors"]}:
            raise DomainError("Unreal actor binding observation mismatch", 409)
