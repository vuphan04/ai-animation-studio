"""Run inside Unreal Editor 5.8 Python. Read-only Asset Registry inventory for the series library (S3).

Reads registry data only: it never loads, edits or saves an asset.
This is an editor-script candidate until it has been run against a real UE 5.8 project.
"""

import json
import os
import traceback

import unreal


REQUEST_FILE = os.environ["STUDIO_SCAN_REQUEST"]
RESULT_FILE = os.environ["STUDIO_SCAN_RESULT"]
UE_TARGET_VERSION = "5.8"
CLASSES = ("World", "Blueprint", "SkeletalMesh", "Skeleton", "AnimSequence", "AnimMontage", "AnimComposite",
           "BlendSpace", "BlendSpace1D", "NiagaraSystem", "IKRetargeter", "IKRigDefinition", "LevelSequence")


def tag_value(asset_data, name):
    value = asset_data.get_tag_value(name)
    return str(value) if value else None


def run(request):
    if request.get("schema_version") != "series.scan-request.1":
        raise RuntimeError("unsupported scan request schema")
    engine_version = unreal.SystemLibrary.get_engine_version()
    if not engine_version.startswith(UE_TARGET_VERSION + "."):
        raise RuntimeError("asset scan targets UE " + UE_TARGET_VERSION + "; observed " + engine_version)
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    registry.wait_for_completion()
    assets = []
    for root in request["roots"]:
        for data in registry.get_assets_by_path(root, recursive=True):
            asset_class = str(data.asset_class_path.asset_name)
            if asset_class not in CLASSES:
                continue
            assets.append({
                "object_path": str(data.package_name) + "." + str(data.asset_name),
                "asset_name": str(data.asset_name),
                "package_path": str(data.package_path),
                "asset_class": asset_class,
                "skeleton_path": tag_value(data, "Skeleton"),
                "parent_class": tag_value(data, "NativeParentClass") or tag_value(data, "ParentClass"),
            })
    assets.sort(key=lambda item: item["object_path"])
    return {"status": "SCANNED", "engine_version": engine_version, "assets": assets}


def main():
    request = json.loads(open(REQUEST_FILE, encoding="utf-8").read())
    try:
        result = run(request)
    except Exception as exc:
        unreal.log_error(traceback.format_exc())
        result = {"status": "BLOCKED", "error": str(exc)}
    result.update({"schema_version": "series.scan-result.1", "adapter_kind": "UNREAL_EDITOR_PYTHON",
                   "operation_id": request["operation_id"]})
    with open(RESULT_FILE, "w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False)


main()
