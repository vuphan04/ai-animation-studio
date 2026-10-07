"""Run inside Unreal Editor 5.8 Python. Renders Style bible reference frames (S4) from mapped assets.

Characters are spawned at the origin of the operator's turnaround level and captured from four angles;
environments are captured from the cameras already placed in their level. Every spawned actor is transient
and destroyed; no level or asset is saved.
This is an editor-script candidate until it has been run against a real UE 5.8 project.
"""

import json
import math
import os
import traceback

import unreal


REQUEST_FILE = os.environ["STUDIO_CAPTURE_REQUEST"]
RESULT_FILE = os.environ["STUDIO_CAPTURE_RESULT"]
UE_TARGET_VERSION = "5.8"
VIEWS = (("turnaround_front", 0.0), ("turnaround_three_quarter", 45.0), ("turnaround_side", 90.0), ("turnaround_back", 180.0))
FOV = 30.0


def actor_subsystem():
    return unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


def load_world(map_path):
    if not unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).load_level(map_path.split(".", 1)[0]):
        raise RuntimeError("could not load level: " + map_path)
    return unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()


def capture(world, location, rotation, fov, request, name):
    camera = actor_subsystem().spawn_actor_from_class(unreal.SceneCapture2D, location, rotation, transient=True)
    try:
        target = unreal.RenderingLibrary.create_render_target2d(
            world, request["width"], request["height"], unreal.TextureRenderTargetFormat.RTF_RGBA8)
        component = camera.capture_component2d
        component.set_editor_property("texture_target", target)
        component.set_editor_property("capture_source", unreal.SceneCaptureSource.SCS_FINAL_COLOR_LDR)
        component.set_editor_property("fov_angle", fov)
        component.capture_scene()
        unreal.RenderingLibrary.export_render_target(world, target, request["output_dir"], name)
    finally:
        actor_subsystem().destroy_actor(camera)
    for candidate in (name, name + ".png"):
        if os.path.isfile(os.path.join(request["output_dir"], candidate)):
            return candidate
    raise RuntimeError("render target export produced no file: " + name)


def capture_character(item, request, images):
    world = load_world(item["map_path"])
    asset = unreal.EditorAssetLibrary.load_asset(item["asset_path"])
    if asset is None:
        raise RuntimeError("asset missing: " + item["asset_path"])
    actor = actor_subsystem().spawn_actor_from_object(asset, unreal.Vector(0.0, 0.0, 0.0), unreal.Rotator(0.0, 0.0, 0.0), transient=True)
    if actor is None:
        raise RuntimeError("asset cannot be placed in a level: " + item["asset_path"])
    try:
        origin, extent = actor.get_actor_bounds(False)
        radius = max(extent.x, extent.y, extent.z, 1.0)
        distance = radius / math.tan(math.radians(FOV / 2.0)) * 1.15
        for role, yaw in VIEWS:
            angle = math.radians(yaw)
            location = unreal.Vector(origin.x + distance * math.cos(angle), origin.y + distance * math.sin(angle), origin.z)
            rotation = unreal.MathLibrary.find_look_at_rotation(location, origin)
            images.append({"entity_key": item["entity_key"], "role": role,
                           "file": capture(world, location, rotation, FOV, request, item["entity_key"] + "_" + role + ".png")})
    finally:
        actor_subsystem().destroy_actor(actor)


def capture_environment(item, request, images):
    world = load_world(item["asset_path"])
    cameras = sorted(unreal.GameplayStatics.get_all_actors_of_class(world, unreal.CameraActor), key=lambda a: a.get_actor_label())
    views = [(c.get_actor_location(), c.get_actor_rotation(), c.camera_component.get_editor_property("field_of_view")) for c in cameras[:4]]
    if not views:
        starts = unreal.GameplayStatics.get_all_actors_of_class(world, unreal.PlayerStart)
        views = [(s.get_actor_location() + unreal.Vector(0.0, 0.0, 80.0), s.get_actor_rotation(), 60.0) for s in starts[:1]]
    if not views:
        raise RuntimeError("level has no CameraActor or PlayerStart to frame a reference")
    for index, (location, rotation, fov) in enumerate(views, start=1):
        role = "frame_%02d" % index
        images.append({"entity_key": item["entity_key"], "role": role,
                       "file": capture(world, location, rotation, fov, request, item["entity_key"] + "_" + role + ".png")})


def run(request):
    if request.get("schema_version") != "series.capture-request.1":
        raise RuntimeError("unsupported capture request schema")
    engine_version = unreal.SystemLibrary.get_engine_version()
    if not engine_version.startswith(UE_TARGET_VERSION + "."):
        raise RuntimeError("style capture targets UE " + UE_TARGET_VERSION + "; observed " + engine_version)
    images, skipped = [], []
    for item in request["items"]:
        try:
            (capture_character if item["kind"] == "character" else capture_environment)(item, request, images)
        except Exception as exc:
            skipped.append({"entity_key": item["entity_key"], "reason": str(exc)})
    return {"status": "CAPTURED", "engine_version": engine_version, "images": images, "skipped": skipped}


def main():
    request = json.loads(open(REQUEST_FILE, encoding="utf-8").read())
    try:
        result = run(request)
    except Exception as exc:
        unreal.log_error(traceback.format_exc())
        result = {"status": "BLOCKED", "error": str(exc)}
    result.update({"schema_version": "series.capture-result.1", "adapter_kind": "UNREAL_EDITOR_PYTHON",
                   "operation_id": request["operation_id"]})
    with open(RESULT_FILE, "w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False)


main()
