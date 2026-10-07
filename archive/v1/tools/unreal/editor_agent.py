"""Run inside Unreal Editor 5.8 Python. Inputs come from STUDIO_P2_OPERATION/RESULT.

This is an editor-script candidate until the Sequencer spike succeeds on a real UE 5.8 project.
"""

import hashlib
import json
import os
import sqlite3
import traceback

import unreal


OP_FILE = os.environ["STUDIO_P2_OPERATION"]
RESULT_FILE = os.environ["STUDIO_P2_RESULT"]
DB_FILE = os.environ["STUDIO_P2_DB"]
MODE = os.environ["STUDIO_P2_MODE"]
OWNER_TAG = unreal.Name("aas_owner")
FINGERPRINT_TAG = unreal.Name("aas_managed_fingerprint")
CACHE_TAG = unreal.Name("aas_retarget_cache_key")
UE_TARGET_VERSION = "5.8"
TRACK_NAME = "[AI Animation Studio] Shots"
ANIM_TRACK_NAME = "[AI Animation Studio] Body"
LIB = unreal.EditorAssetLibrary


def sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def require_asset(path, cls):
    asset = LIB.load_asset(path)
    if asset is None or not isinstance(asset, cls):
        raise RuntimeError("missing or wrong-class UE asset: " + path)
    return asset


def tag(asset, key):
    return str(LIB.get_metadata_tag(asset, key))


def sequence(path, owner, create=False):
    if LIB.does_asset_exist(path):
        asset = require_asset(path, unreal.LevelSequence)
        if tag(asset, OWNER_TAG) != owner:
            raise RuntimeError("existing sequence has no matching app ownership tag: " + path)
        return asset, False
    if not create:
        return None, True
    folder, name = path.rsplit("/", 1)
    asset = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, folder, unreal.LevelSequence, unreal.LevelSequenceFactoryNew())
    if asset is None:
        raise RuntimeError("could not create sequence: " + path)
    LIB.set_metadata_tag(asset, OWNER_TAG, owner)
    return asset, True


def managed_track(master):
    tracks = [t for t in master.find_tracks_by_exact_type(unreal.MovieSceneCinematicShotTrack)
              if str(t.get_editor_property("display_name")) == TRACK_NAME]
    if len(tracks) > 1:
        raise RuntimeError("duplicate app-owned shot tracks")
    return tracks[0] if tracks else None


def observed(master, shots):
    track = managed_track(master) if master else None
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    sections = []
    if track:
        for s in track.get_sections():
            sub = s.get_sequence()
            sections.append({"path": sub.get_path_name().split(".", 1)[0] if sub else None,
                             "in": s.get_start_frame(), "out_exclusive": s.get_end_frame()})
    sections.sort(key=lambda s: (s["in"], s["path"] or ""))
    shot_items = []
    for plan in shots:
        path = plan["sequence_path"]
        seq = LIB.load_asset(path) if LIB.does_asset_exist(path) else None
        bindings = []
        if seq:
            for binding in seq.get_bindings():
                if binding.get_name().startswith("AAS_"):
                    bound = unreal.SequencerTools.get_bound_objects(
                        world, seq, [binding], unreal.SequencerScriptingRange(
                            has_start_value=True, has_end_value=True, inclusive_start=0, exclusive_end=1))
                    resolved_paths = sorted({obj.get_path_name() for item in bound for obj in item.bound_objects})
                    animations = []
                    for anim_track in binding.find_tracks_by_exact_type(unreal.MovieSceneSkeletalAnimationTrack):
                        if str(anim_track.get_editor_property("display_name")) == ANIM_TRACK_NAME:
                            for section in anim_track.get_sections():
                                animation = section.get_editor_property("params").get_editor_property("animation")
                                animations.append({"path": animation.get_path_name() if animation else None,
                                                   "in": section.get_start_frame(), "out_exclusive": section.get_end_frame()})
                    bindings.append({"name": binding.get_name(), "guid": str(binding.get_id()),
                                     "resolved_actor_paths": resolved_paths, "animations": animations})
        shot_items.append({"shot_code": plan["shot_code"], "sequence_path": path,
                           "bindings": sorted(bindings, key=lambda b: b["name"])})
    return {"sections": sections, "shots": shot_items}


def guard_db(op):
    db = sqlite3.connect(DB_FILE, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("BEGIN IMMEDIATE")
    try:
        for shot in op["shots"]:
            row = db.execute("SELECT current_revision FROM shots WHERE id=?", (shot["shot_id"],)).fetchone()
            if row is None or row["current_revision"] != shot["shot_revision"]:
                raise RuntimeError("shot revision changed before Unreal write")
            rev = db.execute("SELECT input_hash FROM shot_revisions WHERE shot_id=? AND revision=?", (shot["shot_id"], shot["shot_revision"])).fetchone()
            if rev is None or rev["input_hash"] != shot["input_hash"]:
                raise RuntimeError("shot input hash changed before Unreal write")
            for subsystem, expected in shot["expected_states"].items():
                state = db.execute("SELECT * FROM subsystem_states WHERE shot_id=? AND subsystem=?", (shot["shot_id"], subsystem)).fetchone()
                if state is None or state["state_revision"] != expected or state["mode"] != "AUTO" or state["lock_state"] != "UNLOCKED":
                    raise RuntimeError("manual, locked or changed subsystem before Unreal write")
                if state["observed_fingerprint"] and state["observed_fingerprint"] != state["synced_fingerprint"]:
                    raise RuntimeError("external edit conflict before Unreal write")
        return db
    except Exception:
        db.rollback()
        db.close()
        raise


def resolved_animation(actor, *, create=False):
    retarget = actor.get("retarget")
    if not retarget:
        return require_asset(actor["animation_path"], unreal.AnimSequenceBase)
    source = require_asset(actor["animation_path"], unreal.AnimSequenceBase)
    source_mesh = require_asset(retarget["source_mesh_path"], unreal.SkeletalMesh)
    target_mesh = require_asset(retarget["target_mesh_path"], unreal.SkeletalMesh)
    profile = require_asset(retarget["profile_path"], unreal.IKRetargeter)
    cache_path = retarget["cache_path"]
    if LIB.does_asset_exist(cache_path):
        cached = require_asset(cache_path, unreal.AnimSequenceBase)
        if tag(cached, CACHE_TAG) != retarget["cache_key"]:
            raise RuntimeError("retarget cache path exists with different profile/source/version")
        return cached
    if not create:
        return source
    source_data = LIB.find_asset_data(actor["animation_path"])
    produced = unreal.IKRetargetBatchOperation.duplicate_and_retarget(
        [source_data], source_mesh, target_mesh, profile, suffix=retarget["suffix"],
        include_referenced_assets=False)
    if len(produced) != 1 or not LIB.does_asset_exist(cache_path):
        raise RuntimeError("retarget result missing or unexpected; inspect before retry")
    cached = require_asset(cache_path, unreal.AnimSequenceBase)
    LIB.set_metadata_tag(cached, CACHE_TAG, retarget["cache_key"])
    if not LIB.save_loaded_asset(cached, False):
        raise RuntimeError("could not save retarget cache")
    return cached


def run(op):
    if op.get("schema_version") != "p2.operation.1" or MODE not in ("preflight", "apply", "observe"):
        raise RuntimeError("unsupported operation schema or mode")
    engine_version = unreal.SystemLibrary.get_engine_version()
    if not engine_version.startswith(UE_TARGET_VERSION + "."):
        raise RuntimeError("editor script targets UE " + UE_TARGET_VERSION + "; observed " + engine_version)
    unsigned = {key: value for key, value in op.items() if key not in ("input_hash", "operation_id")}
    unsigned["shots"] = [{key: value for key, value in shot.items() if key != "expected_states"} for shot in unsigned["shots"]]
    if sha(unsigned) != op.get("input_hash") or sha({"schema": op["schema_version"], "project": op["project_id"],
                                                      "episode": op["episode_id"], "input_hash": op["input_hash"]}) != op.get("operation_id"):
        raise RuntimeError("Unreal operation hash mismatch")
    unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).load_level(op["world_path"])
    actors = {a.get_path_name(): a for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()}
    for plan in op["shots"]:
        plan["sequence_path"] = op["master_path"].rsplit("/", 1)[0] + "/" + plan["shot_code"]
        for performer in plan["actors"]:
            for key in ("actor_path", "anchor_path"):
                if performer[key] not in actors:
                    raise RuntimeError("registered actor missing in loaded world: " + performer[key])
            resolved_animation(performer, create=False)
    master, missing = sequence(op["master_path"], op["project_id"], False)
    for plan in op["shots"]:
        sequence(plan["sequence_path"], op["project_id"], False)
    before = observed(master, op["shots"])
    before_fingerprint = sha(before)
    if master and tag(master, FINGERPRINT_TAG) != before_fingerprint:
        raise RuntimeError("app-managed Sequencer content differs from saved fingerprint; adopt/sync required")
    desired_paths = {s["sequence_path"] for s in op["shots"]}
    if any(s["path"] not in desired_paths for s in before["sections"]):
        raise RuntimeError("managed master contains other shots; select same set or review a master revision")
    if MODE == "preflight":
        changes = []
        if missing:
            changes.append({"action": "CREATE_MASTER", "path": op["master_path"]})
        current_sections = {s["path"]: s for s in before["sections"]}
        current_shots = {s["shot_code"]: s for s in before["shots"]}
        for shot in op["shots"]:
            path = shot["sequence_path"]
            if not LIB.does_asset_exist(path):
                changes.append({"action": "CREATE_SHOT", "path": path})
            if current_sections.get(path) != {"path": path, "in": shot["timeline"]["in"],
                                              "out_exclusive": shot["timeline"]["out_exclusive"]}:
                changes.append({"action": "UPSERT_SECTION", "path": path, "timeline": shot["timeline"]})
            bindings = {b["name"]: b for b in current_shots[shot["shot_code"]]["bindings"]}
            for actor in shot["actors"]:
                name = "AAS_" + actor["character_id"]
                if name not in bindings:
                    changes.append({"action": "ADD_BINDING", "path": path, "character_id": actor["character_id"]})
                target = actor["retarget"]["cache_path"] if actor.get("retarget") else actor["animation_path"]
                expected_anim = {"path": target + "." + target.rsplit("/", 1)[1], "in": 0,
                                 "out_exclusive": shot["timeline"]["out_exclusive"] - shot["timeline"]["in"]}
                if bindings.get(name, {}).get("animations") != [expected_anim]:
                    changes.append({"action": "UPSERT_ANIMATION", "path": path, "character_id": actor["character_id"],
                                    "animation_path": target})
        return {"status": "PREFLIGHT", "missing_master": missing, "observation": before,
                "output_fingerprint": before_fingerprint, "changes": changes}
    if MODE == "observe":
        return {"status": "OBSERVED", "observation": before, "output_fingerprint": before_fingerprint}
    db = guard_db(op)
    try:
        master, _ = sequence(op["master_path"], op["project_id"], True)
        master.set_display_rate(unreal.FrameRate(op["fps"]["num"], op["fps"]["den"]))
        master.set_playback_start(min(s["timeline"]["in"] for s in op["shots"]))
        master.set_playback_end(max(s["timeline"]["out_exclusive"] for s in op["shots"]))
        track = managed_track(master)
        if track is None:
            track = master.add_track(unreal.MovieSceneCinematicShotTrack)
            track.set_editor_property("display_name", TRACK_NAME)
        existing = {}
        for section in track.get_sections():
            sub = section.get_sequence()
            path = sub.get_path_name().split(".", 1)[0] if sub else None
            if path in existing:
                raise RuntimeError("duplicate managed shot section")
            existing[path] = section
        for plan in op["shots"]:
            shot_seq, _ = sequence(plan["sequence_path"], op["project_id"], True)
            shot_seq.set_display_rate(unreal.FrameRate(op["fps"]["num"], op["fps"]["den"]))
            duration = plan["timeline"]["out_exclusive"] - plan["timeline"]["in"]
            shot_seq.set_playback_start(0)
            shot_seq.set_playback_end(duration)
            bindings = {b.get_name(): b for b in shot_seq.get_bindings() if b.get_name().startswith("AAS_")}
            for actor in plan["actors"]:
                name = "AAS_" + actor["character_id"]
                binding = bindings.get(name)
                if binding is None:
                    binding = shot_seq.add_possessable(actors[actor["actor_path"]])
                    binding.set_name(name)
                tracks = [t for t in binding.find_tracks_by_exact_type(unreal.MovieSceneSkeletalAnimationTrack)
                          if str(t.get_editor_property("display_name")) == ANIM_TRACK_NAME]
                if len(tracks) > 1:
                    raise RuntimeError("duplicate managed animation tracks")
                anim_track = tracks[0] if tracks else binding.add_track(unreal.MovieSceneSkeletalAnimationTrack)
                if not tracks:
                    anim_track.set_editor_property("display_name", ANIM_TRACK_NAME)
                sections = list(anim_track.get_sections())
                if len(sections) > 1:
                    raise RuntimeError("duplicate managed animation sections")
                anim_section = sections[0] if sections else anim_track.add_section()
                params = anim_section.get_editor_property("params")
                params.set_editor_property("animation", resolved_animation(actor, create=True))
                anim_section.set_editor_property("params", params)
                anim_section.set_range(0, duration)
            section = existing.get(plan["sequence_path"])
            if section is None:
                section = track.add_section()
            section.set_sequence(shot_seq)
            section.set_range(plan["timeline"]["in"], plan["timeline"]["out_exclusive"])
            if not LIB.save_loaded_asset(shot_seq, False):
                raise RuntimeError("failed to save shot sequence")
        after = observed(master, op["shots"])
        fingerprint = sha(after)
        LIB.set_metadata_tag(master, FINGERPRINT_TAG, fingerprint)
        if not LIB.save_loaded_asset(master, False):
            raise RuntimeError("failed to save master sequence")
        db.commit()
        return {"status": "APPLIED", "observation": after, "output_fingerprint": fingerprint}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main():
    op = json.loads(open(OP_FILE, encoding="utf-8").read())
    try:
        result = run(op)
        result.update({"schema_version": "p2.result.1", "adapter_kind": "UNREAL_EDITOR_PYTHON",
                       "engine_version": unreal.SystemLibrary.get_engine_version(),
                       "operation_id": op["operation_id"], "input_hash": op["input_hash"],
                       "master_path": op["master_path"], "world_path": op["world_path"]})
        observed_shots = {s["shot_code"]: s for s in result["observation"]["shots"]}
        result["shots"] = [{"shot_code": s["shot_code"], "timeline": s["timeline"],
                             "sequence_path": observed_shots[s["shot_code"]]["sequence_path"],
                             "bindings": [{"character_id": a["character_id"], "actor_path": a["actor_path"],
                                           "animation_id": a["animation_id"], "animation_version": a["animation_version"],
                                           "animation_path": a["retarget"]["cache_path"] if a.get("retarget") else a["animation_path"],
                                           "retarget_profile_id": a["retarget"]["profile_id"] if a.get("retarget") else None,
                                           "retarget_profile_version": a["retarget"]["profile_version"] if a.get("retarget") else None,
                                           "paired_asset_id": a.get("paired_asset_id"), "sync_point_local_frame": a.get("sync_point_local_frame"),
                                           "binding_guid": next((b["guid"] for b in observed_shots[s["shot_code"]]["bindings"]
                                                                 if b["name"] == "AAS_" + a["character_id"]), None)}
                                          for a in s["actors"]]}
                            for s in op["shots"]]
    except Exception as exc:
        unreal.log_error(traceback.format_exc())
        result = {"schema_version": "p2.result.1", "adapter_kind": "UNREAL_EDITOR_PYTHON",
                  "operation_id": op["operation_id"], "input_hash": op["input_hash"],
                  "status": "BLOCKED", "error": str(exc)}
    with open(RESULT_FILE, "w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)


main()
