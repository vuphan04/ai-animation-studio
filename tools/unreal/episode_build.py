"""Run inside Unreal Editor 5.8 Python. Builds what workflow V4.5 steps 07 and 08-b6 need for one episode.

ASSEMBLE: the master sequence, one level sequence per shot, a spawnable per character, a camera with a
          camera cut, and the approved dialogue takes on an audio track.
ANIMATE:  batch retarget into the episode folder, then the animations on each character's Body track.

Only sequences carrying this app's ownership tag and only the tracks named below are written; anything the
operator added by hand in a sequence is left alone. Each shot reports its own result, so one failing shot
does not hide the others.
This is an editor-script candidate until it has been run against a real UE 5.8 project.
"""

import json
import os
import traceback

import unreal


REQUEST_FILE = os.environ["STUDIO_BUILD_REQUEST"]
RESULT_FILE = os.environ["STUDIO_BUILD_RESULT"]
UE_TARGET_VERSION = "5.8"
OWNER_TAG = unreal.Name("aas_owner")
SHOT_TRACK = "[AI Animation Studio] Shots"
DIALOGUE_TRACK = "[AI Animation Studio] Dialogue"
BODY_TRACK = "[AI Animation Studio] Body"
CAMERA_NAME = "AAS_Camera"
FOCAL_LENGTH = {"EXTREME_WIDE": 18.0, "WIDE": 24.0, "MEDIUM": 35.0, "TWO_SHOT": 35.0, "OVER_SHOULDER": 50.0,
                "CLOSE_UP": 85.0, "EXTREME_CLOSE_UP": 100.0, "INSERT": 100.0}
LIB = unreal.EditorAssetLibrary


def package(path):
    return path.split(".", 1)[0]


def load(path, cls, label):
    asset = LIB.load_asset(package(path)) if path else None
    if asset is None or not isinstance(asset, cls):
        raise RuntimeError(label + " missing or of the wrong class: " + str(path))
    return asset


def create(path, cls, factory):
    folder, name = package(path).rsplit("/", 1)
    asset = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, folder, cls, factory)
    if asset is None:
        raise RuntimeError("could not create asset: " + path)
    return asset


def save(asset):
    if not LIB.save_loaded_asset(asset, False):
        raise RuntimeError("could not save asset: " + asset.get_path_name())


def owned_sequence(path, owner, create_missing):
    if LIB.does_asset_exist(package(path)):
        sequence = load(path, unreal.LevelSequence, "sequence")
        if str(LIB.get_metadata_tag(sequence, OWNER_TAG)) != owner:
            raise RuntimeError("a sequence not created by this app already exists at " + path)
        return sequence
    if not create_missing:
        raise RuntimeError("sequence does not exist yet; run step 07 first: " + path)
    sequence = create(path, unreal.LevelSequence, unreal.LevelSequenceFactoryNew())
    LIB.set_metadata_tag(sequence, OWNER_TAG, owner)
    return sequence


def named_track(container, cls, name):
    tracks = [t for t in container.find_tracks_by_exact_type(cls) if str(t.get_editor_property("display_name")) == name]
    if len(tracks) > 1:
        raise RuntimeError("duplicate app-owned track: " + name)
    if tracks:
        return tracks[0]
    track = container.add_track(cls)
    track.set_editor_property("display_name", name)
    return track


def binding_named(sequence, name):
    return next((b for b in sequence.get_bindings() if str(b.get_name()) == name), None)


def clear(track):
    for section in list(track.get_sections()):
        track.remove_section(section)


def frame_rate(fps):
    return unreal.FrameRate(fps["num"], fps["den"])


def import_sound(item):
    if LIB.does_asset_exist(package(item["sound_path"])):
        return load(item["sound_path"], unreal.SoundWave, "dialogue sound")
    if not os.path.isfile(item["wav_path"]):
        raise RuntimeError("dialogue WAV missing on disk: " + item["wav_path"])
    folder, name = package(item["sound_path"]).rsplit("/", 1)
    task = unreal.AssetImportTask()
    task.set_editor_property("filename", item["wav_path"])
    task.set_editor_property("destination_path", folder)
    task.set_editor_property("destination_name", name)
    task.set_editor_property("automated", True)
    task.set_editor_property("replace_existing", False)
    task.set_editor_property("save", True)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    return load(item["sound_path"], unreal.SoundWave, "imported dialogue sound")


def assemble_shot(shot, request, notes):
    if not LIB.does_asset_exist(package(shot["map_path"])):
        raise RuntimeError("environment level missing: " + shot["map_path"])
    sequence = owned_sequence(shot["sequence_path"], request["project_id"], True)
    sequence.set_display_rate(frame_rate(request["fps"]))
    sequence.set_playback_start(0)
    sequence.set_playback_end(shot["length_frames"])
    for character in shot["characters"]:
        if binding_named(sequence, character["binding_name"]):
            continue
        asset = LIB.load_asset(package(character["asset_path"]))
        if asset is None:
            raise RuntimeError("character asset missing: " + character["asset_path"])
        binding = sequence.add_spawnable_from_class(asset.generated_class()) if isinstance(asset, unreal.Blueprint) else sequence.add_spawnable_from_instance(asset)
        binding.set_name(character["binding_name"])
        notes.append("added " + character["binding_name"])
    if binding_named(sequence, CAMERA_NAME) is None:
        camera = sequence.add_spawnable_from_class(unreal.CineCameraActor)
        camera.set_name(CAMERA_NAME)
        try:
            camera.get_object_template().get_cine_camera_component().set_editor_property("current_focal_length", FOCAL_LENGTH.get(shot["shot_size"], 35.0))
        except Exception as exc:
            notes.append("camera focal length left at default: " + str(exc))
        if not sequence.find_tracks_by_exact_type(unreal.MovieSceneCameraCutTrack):
            cut = sequence.add_track(unreal.MovieSceneCameraCutTrack).add_section()
            cut.set_range(0, shot["length_frames"])
            make_id = getattr(sequence, "get_binding_id", None) or getattr(sequence, "make_binding_id")
            cut.set_camera_binding_id(make_id(camera))
        notes.append("added default camera at the level origin; place it in Sequencer")
    track = named_track(sequence, unreal.MovieSceneAudioTrack, DIALOGUE_TRACK)
    clear(track)
    for position, item in enumerate(shot["audio"]):
        section = track.add_section()
        section.set_sound(import_sound(item))
        section.set_range(item["start_frame"], item["start_frame"] + item["frames"])
        section.set_row_index(position % 2)
    save(sequence)
    notes.append("level for this shot: " + shot["map_path"])


def assemble_master(request):
    master = owned_sequence(request["master"]["path"], request["project_id"], True)
    master.set_display_rate(frame_rate(request["fps"]))
    master.set_playback_start(0)
    master.set_playback_end(request["master"]["total_frames"])
    track = named_track(master, unreal.MovieSceneCinematicShotTrack, SHOT_TRACK)
    existing = {}
    for section in track.get_sections():
        sub = section.get_sequence()
        existing[package(sub.get_path_name()) if sub else None] = section
    for item in request["master"]["sections"]:
        path = package(item["sequence_path"])
        if not LIB.does_asset_exist(path):
            continue
        section = existing.get(path) or track.add_section()
        section.set_sequence(load(path, unreal.LevelSequence, "shot sequence"))
        section.set_range(item["start_frame"], item["start_frame"] + item["length_frames"])
    save(master)


def make_ik_rig(mesh, path):
    if LIB.does_asset_exist(package(path)):
        return load(path, unreal.IKRigDefinition, "IK rig")
    rig = create(path, unreal.IKRigDefinition, unreal.IKRigDefinitionFactory())
    controller = unreal.IKRigController.get_controller(rig)
    controller.set_skeletal_mesh(mesh)
    controller.apply_auto_generated_retarget_definition()
    save(rig)
    return rig


def make_retargeter(item, source_mesh, target_mesh, notes):
    path = item["create_retargeter_path"]
    if LIB.does_asset_exist(package(path)):
        return load(path, unreal.IKRetargeter, "IK retargeter")
    retargeter = create(path, unreal.IKRetargeter, unreal.IKRetargetFactory())
    controller = unreal.IKRetargeterController.get_controller(retargeter)
    controller.set_ik_rig(unreal.RetargetSourceOrTarget.SOURCE, make_ik_rig(source_mesh, path + "_SourceRig"))
    controller.set_ik_rig(unreal.RetargetSourceOrTarget.TARGET, make_ik_rig(target_mesh, path + "_TargetRig"))
    controller.auto_map_chains(unreal.AutoMapChainType.FUZZY, True)
    save(retargeter)
    notes.append("created retargeter " + path + "; open it and check the pose before locking C5")
    return retargeter


def retarget(item, notes):
    output = package(item["output_path"])
    if LIB.does_asset_exist(output):
        return "REUSED"
    source_mesh = load(item["source_mesh_path"], unreal.SkeletalMesh, "source mesh")
    target_mesh = load(item["target_mesh_path"], unreal.SkeletalMesh, "target mesh")
    retargeter = load(item["retargeter_path"], unreal.IKRetargeter, "IK retargeter") if item["retargeter_path"] else make_retargeter(item, source_mesh, target_mesh, notes)
    source = package(item["source_animation"])
    data = LIB.find_asset_data(source)
    # UE 5.8 names the batch entry point run_batch_retarget; duplicate_and_retarget is the deprecated one.
    batch = unreal.IKRetargetBatchOperation
    function = getattr(batch, "run_batch_retarget", None) or batch.duplicate_and_retarget
    suffix = "__AAS_RETARGET"
    try:
        function([data], source_mesh, target_mesh, retargeter, suffix=suffix)
    except TypeError as exc:
        raise RuntimeError("batch retarget call rejected (" + str(exc) + "); signature: " + str(function.__doc__)[:400])
    produced = source + suffix
    if not LIB.does_asset_exist(produced):
        raise RuntimeError("batch retarget produced no asset at " + produced)
    if not LIB.rename_asset(produced, output):
        raise RuntimeError("could not move retargeted animation into the episode folder: " + output)
    LIB.save_asset(output, False)
    return "CREATED"


def animate_shot(shot, request, notes):
    sequence = owned_sequence(shot["sequence_path"], request["project_id"], False)
    for item in shot["bindings"]:
        binding = binding_named(sequence, item["binding_name"])
        if binding is None:
            raise RuntimeError("character binding missing in the shot sequence: " + item["binding_name"])
        track = named_track(binding, unreal.MovieSceneSkeletalAnimationTrack, BODY_TRACK)
        clear(track)
        for part in item["sections"]:
            section = track.add_section()
            params = section.get_editor_property("params")
            params.set_editor_property("animation", load(part["animation_path"], unreal.AnimSequenceBase, "animation"))
            section.set_editor_property("params", params)
            section.set_range(part["start_frame"], part["end_frame"])
            section.set_row_index(part["row"])
            if part["layer"] == "UPPER_BODY":
                notes.append(item["binding_name"] + ": upper-body clip placed on its own row; layering needs an additive or layered setup, check it in Sequencer")
            elif part["blend_frames"]:
                notes.append(item["binding_name"] + ": clips overlap by " + str(part["blend_frames"]) + " frames; set the blend curve in Sequencer if needed")
    save(sequence)


def run(request):
    if request.get("schema_version") != "v45.ue-build-request.1" or request.get("kind") not in ("ASSEMBLE", "ANIMATE"):
        raise RuntimeError("unsupported build request schema or kind")
    engine_version = unreal.SystemLibrary.get_engine_version()
    if not engine_version.startswith(UE_TARGET_VERSION + "."):
        raise RuntimeError("episode build targets UE " + UE_TARGET_VERSION + "; observed " + engine_version)
    retargets, failed = [], {}
    for item in request.get("retargets", []):
        notes = []
        try:
            retargets.append({"output_path": item["output_path"], "status": retarget(item, notes), "notes": notes})
        except Exception as exc:
            unreal.log_error(traceback.format_exc())
            failed[item["output_path"]] = str(exc)
            retargets.append({"output_path": item["output_path"], "status": "FAILED", "error": str(exc), "notes": notes})
    shots = []
    for shot in request["shots"]:
        notes = []
        try:
            if request["kind"] == "ASSEMBLE":
                assemble_shot(shot, request, notes)
            else:
                broken = [part["animation_path"] for item in shot["bindings"] for part in item["sections"] if part["animation_path"] in failed]
                if broken:
                    raise RuntimeError("retarget failed for " + broken[0] + ": " + failed[broken[0]])
                animate_shot(shot, request, notes)
            shots.append({"shot_id": shot["shot_id"], "status": "OK", "notes": notes})
        except Exception as exc:
            unreal.log_error(traceback.format_exc())
            shots.append({"shot_id": shot["shot_id"], "status": "FAILED", "error": str(exc), "notes": notes})
    if request["kind"] == "ASSEMBLE":
        assemble_master(request)
    return {"status": "APPLIED", "engine_version": engine_version, "shots": shots, "retargets": retargets}


def main():
    request = json.loads(open(REQUEST_FILE, encoding="utf-8").read())
    try:
        result = run(request)
    except Exception as exc:
        unreal.log_error(traceback.format_exc())
        result = {"status": "BLOCKED", "error": str(exc)}
    result.update({"schema_version": "v45.ue-build-result.1", "adapter_kind": "UNREAL_EDITOR_PYTHON", "operation_id": request["operation_id"]})
    with open(RESULT_FILE, "w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False)


main()
