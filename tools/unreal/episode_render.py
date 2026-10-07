"""Run inside Unreal Editor 5.8 Python. Prepares a render of workflow V4.5 shots: the proxy of step 09 or the final of step 10.

For every requested shot this script looks at its sequence (camera cut, cache tracks) and adds a job to the
Movie Render Queue: an image sequence into the folder the app asked for, at the size and quality of the
request. The queue is then written to a manifest file. Nothing is rendered here; the app starts the render
as a second Unreal process with that manifest, and no sequence or level is modified.
This is an editor-script candidate until it has been run against a real UE 5.8 project.
"""

import json
import os
import traceback

import unreal


REQUEST_FILE = os.environ["STUDIO_RENDER_REQUEST"]
RESULT_FILE = os.environ["STUDIO_RENDER_RESULT"]
UE_TARGET_VERSION = "5.8"
OWNER_TAG = unreal.Name("aas_owner")
LIB = unreal.EditorAssetLibrary
# Track class names that mean a simulation is played back from a cache instead of being simulated live.
CACHE_TRACKS = {"HAIR": ("GroomCache",), "CLOTH": ("ChaosCache", "GeometryCache"), "VFX": ("NiagaraCache",)}
IMAGE_OUTPUT = {"JPG": "MoviePipelineImageSequenceOutput_JPG", "PNG": "MoviePipelineImageSequenceOutput_PNG"}


def package(path):
    return path.split(".", 1)[0]


def all_tracks(sequence):
    tracks = list(sequence.get_tracks())
    for binding in sequence.get_bindings():
        tracks += list(binding.get_tracks())
    return tracks


def inspect(sequence):
    names = sorted({track.get_class().get_name() for track in all_tracks(sequence)})
    found = {kind: any(marker in name for name in names for marker in markers) for kind, markers in CACHE_TRACKS.items()}
    return {"cache_tracks": found, "track_classes": names,
            "camera_cut": bool(sequence.find_tracks_by_exact_type(unreal.MovieSceneCameraCutTrack))}


def add_job(queue, shot, request, notes):
    job = queue.allocate_new_job(unreal.MoviePipelineExecutorJob)
    job.set_editor_property("job_name", shot["shot_id"])
    job.set_editor_property("sequence", unreal.SoftObjectPath(shot["sequence_path"] + "." + shot["sequence_path"].rsplit("/", 1)[1]))
    job.set_editor_property("map", unreal.SoftObjectPath(shot["map_path"]))
    config = job.get_configuration()
    output = config.find_or_add_setting_by_class(unreal.MoviePipelineOutputSetting)
    output.set_editor_property("output_directory", unreal.DirectoryPath(os.path.join(request["output_dir"], shot["shot_id"])))
    output.set_editor_property("file_name_format", "frame.{frame_number}")
    output.set_editor_property("zero_pad_frame_numbers", 4)
    output.set_editor_property("output_resolution", unreal.IntPoint(request["width"], request["height"]))
    output.set_editor_property("use_custom_frame_rate", True)
    output.set_editor_property("output_frame_rate", unreal.FrameRate(request["fps"]["num"], request["fps"]["den"]))
    output.set_editor_property("override_existing_output", True)
    config.find_or_add_setting_by_class(unreal.MoviePipelineDeferredPassBase)
    config.find_or_add_setting_by_class(getattr(unreal, IMAGE_OUTPUT[request["image_format"]]))
    if request["temporal_samples"] > 1:
        try:
            quality = config.find_or_add_setting_by_class(unreal.MoviePipelineAntiAliasingSetting)
            quality.set_editor_property("temporal_sample_count", request["temporal_samples"])
        except Exception as exc:
            notes.append("anti-aliasing samples left at the project default: " + str(exc))


def run(request):
    if request.get("schema_version") != "v45.ue-render-request.1" or request.get("quality") not in ("PROXY", "FINAL") or request.get("image_format") not in IMAGE_OUTPUT:
        raise RuntimeError("unsupported render request schema, quality or image format")
    engine_version = unreal.SystemLibrary.get_engine_version()
    if not engine_version.startswith(UE_TARGET_VERSION + "."):
        raise RuntimeError("episode render targets UE " + UE_TARGET_VERSION + "; observed " + engine_version)
    queue = unreal.get_editor_subsystem(unreal.MoviePipelineQueueSubsystem).get_queue()
    queue.delete_all_jobs()
    shots = []
    for shot in request["shots"]:
        notes = []
        try:
            path = package(shot["sequence_path"])
            sequence = LIB.load_asset(path) if LIB.does_asset_exist(path) else None
            if sequence is None or not isinstance(sequence, unreal.LevelSequence):
                raise RuntimeError("shot sequence missing: " + path)
            if str(LIB.get_metadata_tag(sequence, OWNER_TAG)) != request["project_id"]:
                raise RuntimeError("shot sequence was not created by this app: " + path)
            if not LIB.does_asset_exist(package(shot["map_path"])):
                raise RuntimeError("environment level missing: " + shot["map_path"])
            seen = inspect(sequence)
            if not seen["camera_cut"]:
                raise RuntimeError("shot sequence has no camera cut track; nothing would be framed")
            add_job(queue, shot, request, notes)
            shots.append({"shot_id": shot["shot_id"], "status": "OK", "inspection": seen, "notes": notes})
        except Exception as exc:
            unreal.log_error(traceback.format_exc())
            shots.append({"shot_id": shot["shot_id"], "status": "FAILED", "error": str(exc), "notes": notes})
    manifest = None
    if any(shot["status"] == "OK" for shot in shots):
        saved = unreal.MoviePipelineEditorLibrary.save_queue_to_manifest_file(queue)
        manifest = next((item for item in (saved if isinstance(saved, (tuple, list)) else (saved,)) if isinstance(item, str) and item), None)
        if not manifest:
            raise RuntimeError("could not write the Movie Render Queue manifest")
    return {"status": "PREPARED", "engine_version": engine_version, "manifest": manifest, "shots": shots}


def main():
    request = json.loads(open(REQUEST_FILE, encoding="utf-8").read())
    try:
        result = run(request)
    except Exception as exc:
        unreal.log_error(traceback.format_exc())
        result = {"status": "BLOCKED", "error": str(exc)}
    result.update({"schema_version": "v45.ue-render-result.1", "adapter_kind": "UNREAL_EDITOR_PYTHON", "operation_id": request["operation_id"]})
    with open(RESULT_FILE, "w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False)


main()
