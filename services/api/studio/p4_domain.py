"""Pure P4 cache and render checks. These do not assert Unreal playback."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .domain import DomainError, digest


CACHE_KINDS = ("hair", "cloth", "vfx")
SOURCE_FIELDS = frozenset({"body_artifact_id", "asset_version_id", "collision_fingerprint", "wind_fingerprint",
                           "physics_fingerprint", "seed", "ue_version", "plugin_version", "view_lod_dependency",
                           "camera_fingerprint", "warmup_frames", "handle_before", "handle_after"})


def validate_source(source: dict, subsystem: str) -> dict:
    if subsystem not in CACHE_KINDS or not isinstance(source, dict) or set(source) != SOURCE_FIELDS:
        raise DomainError("Nguồn cache cần đủ body/asset/collision/wind/physics/seed/range/UE-plugin/view-LOD.")
    for key in ("body_artifact_id", "asset_version_id", "collision_fingerprint", "wind_fingerprint",
                "physics_fingerprint", "ue_version", "plugin_version"):
        if not isinstance(source[key], str) or not source[key].strip():
            raise DomainError(f"Nguồn cache thiếu {key}.")
    for key in ("seed", "warmup_frames", "handle_before", "handle_after"):
        if type(source[key]) is not int or source[key] < 0:
            raise DomainError(f"Nguồn cache: {key} phải là số nguyên không âm.")
    if type(source["view_lod_dependency"]) is not bool:
        raise DomainError("view_lod_dependency phải là true/false.")
    camera = source["camera_fingerprint"]
    if source["view_lod_dependency"] and (not isinstance(camera, str) or not camera):
        raise DomainError("Cache phụ thuộc view/LOD cần camera_fingerprint.")
    if not source["view_lod_dependency"] and camera is not None:
        raise DomainError("Cache độc lập view/LOD phải để camera_fingerprint=null.")
    return source


def required_range(timeline: dict, source: dict) -> tuple[int, int]:
    return (timeline["in"] - source["warmup_frames"] - source["handle_before"],
            timeline["out_exclusive"] + source["handle_after"])


def fingerprint(subsystem: str, source: dict, asset_checksum: str | None,
                body_input_hash: str, required_frame_range: tuple[int, int]) -> str:
    return digest({"subsystem": subsystem, "source": source,
                   "asset_checksum": asset_checksum, "body_input_hash": body_input_hash,
                   "required_frame_range": required_frame_range})


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def cache_blockers(cache: dict | None, *, expected_fingerprint: str | None,
                   required_in: int, required_out: int, path: Path | None) -> list[str]:
    if cache is None:
        return ["thiếu bản cache"]
    problems = []
    if cache["validity"] != "CURRENT":
        problems.append("cache STALE")
    if expected_fingerprint is None or cache["input_fingerprint"] != expected_fingerprint:
        problems.append("fingerprint nguồn cache không khớp")
    if cache["range_in"] > required_in or cache["range_out_exclusive"] < required_out:
        problems.append("cache không phủ warm-up/handles/frame range")
    if path is None or not path.is_file():
        problems.append("thiếu file cache")
    elif file_sha256(path) != cache["output_sha256"]:
        problems.append("checksum file cache không khớp")
    if cache["review"] != "APPROVED":
        problems.append("cache chưa được người dùng duyệt")
    if cache["playback_status"] != "VERIFIED":
        problems.append("playback UE chưa được kiểm chứng")
    return problems


def frame_inventory(folder: Path, start: int, end: int, extension: str = ".png") -> dict:
    if end <= start or end - start > 10000:
        raise DomainError("Frame range của render không hợp lệ.")
    frames = []
    missing = []
    for frame in range(start, end):
        path = folder / f"frame_{frame:06d}{extension}"
        if path.resolve().is_relative_to(folder.resolve()) and path.is_file() and path.stat().st_size > 0:
            frames.append({"frame": frame, "bytes": path.stat().st_size, "sha256": file_sha256(path)})
        else:
            missing.append(frame)
    return {"frames": frames, "missing": missing, "expected_count": end - start,
            "state": "PARTIAL" if missing else "FILES_COMPLETE_UNVERIFIED"}
