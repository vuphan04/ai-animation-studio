"""Pure rules for the series library: entity extraction, UE scan inventory and name suggestions."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from .domain import DomainError


ENTITY_KINDS = ("character", "environment")
# What a model's answer may never carry: approvals, locks, asset paths, secrets or anything executable.
FORBIDDEN_KEYS = {"approved", "approval", "lock", "locked", "lock_state", "ue_path", "asset_path",
                  "resolved_path", "resolved_registry", "bone_map", "transform", "api_key", "secret",
                  "command", "shell_command", "execute", "skip_checkpoints"}
VIDEO_MODELS = ("SEEDANCE_2_5", "MINIMAX_H3")
COLOR_SPACES = ("Rec.709", "sRGB", "ACEScg")
CLASS_CATEGORY = {"World": "map", "SkeletalMesh": "skeletal_mesh", "Skeleton": "skeleton",
                  "AnimSequence": "animation", "AnimMontage": "animation", "AnimComposite": "animation",
                  "BlendSpace": "animation", "BlendSpace1D": "animation", "NiagaraSystem": "niagara",
                  "IKRetargeter": "ik_retargeter", "IKRigDefinition": "ik_rig", "LevelSequence": "level_sequence"}
MAPPABLE = {"character": ("character_bp", "metahuman", "blueprint", "skeletal_mesh"), "environment": ("map",)}
SCENE_HEADING = re.compile(r"^(?:INT\.|EXT\.|INT/EXT\.|NỘI\.|NGOẠI\.|CẢNH\s+\d+|SCENE\s+\d+)", re.IGNORECASE)
ASSET_PREFIX = re.compile(r"^(?:BP|MH|SK|SKM|SM|L|LV|MAP|M|NS|AS|A|RTG|IK)_", re.IGNORECASE)


def _forbidden_keys(value: object, path: str = "draft") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in FORBIDDEN_KEYS:
                raise DomainError(f"Planner không được cấp {path}.{key}; chỉ người dùng/backend có quyền duyệt, khóa và gắn asset path.", 409)
            _forbidden_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _forbidden_keys(child, f"{path}[{index}]")


def fold(text: str) -> str:
    """Lower-case ASCII form used only for matching, never for display."""
    text = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def entity_key(kind: str, name: str, taken: set[str]) -> str:
    base = ("CHAR_" if kind == "character" else "ENV_") + (fold(name).upper().replace(" ", "_")[:40] or "ITEM")
    key, index = base, 2
    while key in taken:
        key, index = f"{base}_{index}", index + 1
    return key


def _clean_name(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\([^)]*\)", "", value)).strip(" .-–—:")


def extract_fixture(text: str) -> dict:
    """Rule-based listing for control-flow tests. It is not an AI reading of the screenplay."""
    characters: dict[str, dict] = {}
    environments: dict[str, dict] = {}
    scene_label = "Cảnh 1"
    scene_count = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if SCENE_HEADING.match(stripped):
            scene_count += 1
            scene_label = f"Cảnh {scene_count}"
            place = _clean_name(re.sub(SCENE_HEADING, "", stripped).split(" - ")[0])
            if place:
                item = environments.setdefault(fold(place), {"name": place, "aliases": [], "description": stripped[:200], "scenes": []})
                if scene_label not in item["scenes"]:
                    item["scenes"].append(scene_label)
            continue
        speaker = re.match(r"^([^:]{1,40}):\s+\S", stripped)
        name = _clean_name(speaker.group(1)) if speaker else (_clean_name(stripped) if stripped.isupper() else "")
        if not name or len(name.split()) > 4 or not re.search(r"[^\W\d_]", name):
            continue
        item = characters.setdefault(fold(name), {"name": name.title() if name.isupper() else name, "aliases": [],
                                                  "description": "", "scenes": []})
        if name != item["name"] and name.title() != item["name"] and name not in item["aliases"]:
            item["aliases"].append(name)
        if scene_label not in item["scenes"]:
            item["scenes"].append(scene_label)
    return {"characters": list(characters.values()), "environments": list(environments.values())}


def validate_extraction(value: object) -> dict:
    """The model lists names only; it cannot attach asset paths, approvals or commands."""
    _forbidden_keys(value, "extraction")
    if not isinstance(value, dict) or set(value) != {"characters", "environments"}:
        raise DomainError("Kết quả liệt kê phải có đúng hai danh sách characters và environments.")
    result = {}
    for group in ("characters", "environments"):
        items = value[group]
        if not isinstance(items, list) or len(items) > 200:
            raise DomainError("Danh sách nhân vật/bối cảnh không hợp lệ hoặc quá 200 mục.")
        clean, seen = [], set()
        for item in items:
            if not isinstance(item, dict) or set(item) != {"name", "aliases", "description", "scenes"}:
                raise DomainError("Mỗi mục cần name, aliases, description và scenes.")
            name = item["name"].strip() if isinstance(item["name"], str) else ""
            lists_ok = all(isinstance(item[key], list) and all(isinstance(v, str) for v in item[key]) for key in ("aliases", "scenes"))
            if not name or len(name) > 80 or not isinstance(item["description"], str) or not lists_ok or fold(name) in seen:
                raise DomainError("Tên nhân vật/bối cảnh thiếu, quá dài hoặc trùng nhau.")
            seen.add(fold(name))
            clean.append({"name": name, "aliases": [a.strip()[:80] for a in item["aliases"] if a.strip()][:20],
                          "description": item["description"].strip()[:400],
                          "scenes": [s.strip()[:80] for s in item["scenes"] if s.strip()][:200]})
        result[group] = clean
    return result


def _asset_ref(value: object) -> str | None:
    """Registry tags hold `Class'/Game/Path.Name'`; keep only the object path."""
    if not isinstance(value, str) or not value:
        return None
    match = re.search(r"'([^']+)'", value)
    return match.group(1) if match else value


def categorize(asset_class: str, package_path: str, parent_class: str | None) -> str | None:
    if asset_class == "Blueprint":
        if "/MetaHumans/" in package_path + "/":
            return "metahuman"
        return "character_bp" if parent_class and re.search(r"Character|Pawn", parent_class) else "blueprint"
    return CLASS_CATEGORY.get(asset_class)


def validate_scan_result(result: object, operation_id: str, target_version: str) -> tuple[str, list[dict]]:
    if not isinstance(result, dict) or result.get("schema_version") != "series.scan-result.1" or result.get("adapter_kind") != "UNREAL_EDITOR_PYTHON" or result.get("operation_id") != operation_id or result.get("status") != "SCANNED":
        raise DomainError("Kết quả quét Unreal sai schema, nguồn hoặc operation.", 409)
    version = result.get("engine_version")
    if not isinstance(version, str) or not version.startswith(target_version + "."):
        raise DomainError(f"App viết cho Unreal {target_version}; lần quét báo phiên bản {version!r}.", 409)
    assets = result.get("assets")
    if not isinstance(assets, list) or len(assets) > 500_000:
        raise DomainError("Danh sách asset Unreal không hợp lệ.", 409)
    clean, seen = [], set()
    for item in assets:
        if not isinstance(item, dict) or any(not isinstance(item.get(key), str) or not item[key] for key in ("object_path", "asset_name", "package_path", "asset_class")):
            raise DomainError("Một dòng asset Unreal thiếu đường dẫn, tên hoặc loại.", 409)
        path = item["object_path"]
        if not path.startswith("/") or len(path) > 512 or any(ord(ch) < 32 for ch in path) or path in seen:
            raise DomainError("Đường dẫn asset Unreal không hợp lệ hoặc trùng.", 409)
        seen.add(path)
        parent = _asset_ref(item.get("parent_class"))
        category = categorize(item["asset_class"], item["package_path"], parent)
        if category:
            clean.append({"object_path": path, "asset_name": item["asset_name"], "package_path": item["package_path"],
                          "asset_class": item["asset_class"], "category": category,
                          "skeleton_path": _asset_ref(item.get("skeleton_path")), "parent_class": parent})
    return version, clean


def similarity(names: list[str], asset_name: str) -> float:
    target = fold(ASSET_PREFIX.sub("", asset_name))
    if not target:
        return 0.0
    best = 0.0
    for name in names:
        source = fold(name)
        if not source:
            continue
        score = SequenceMatcher(None, source, target).ratio()
        shared = set(source.split()) & set(target.split())
        if shared:
            score = max(score, 0.6 + 0.4 * len(shared) / max(len(source.split()), len(target.split())))
        best = max(best, score)
    return round(best, 3)


def suggest(names: list[str], assets: list[dict], limit: int = 5) -> list[dict]:
    scored = [{"object_path": a["object_path"], "asset_name": a["asset_name"], "category": a["category"],
               "score": similarity(names, a["asset_name"])} for a in assets]
    scored = [item for item in scored if item["score"] >= 0.45]
    scored.sort(key=lambda item: (-item["score"], item["object_path"]))
    return scored[:limit]
