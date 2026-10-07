"""What every part of the app shares: its error type, canonical hashing and safe project-relative paths."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


# The app is written for this Unreal Engine version; its editor scripts and result checks refuse any other.
UE_TARGET_VERSION = "5.8"


class DomainError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def canonical(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(data: object) -> str:
    return hashlib.sha256(canonical(data).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_relative(root: Path, value: str) -> str:
    path = Path(value)
    if path.is_absolute() or not value or ":" in value or "\\" in value:
        raise DomainError("relative_path must be relative to the project root")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()) or resolved == root.resolve():
        raise DomainError("relative_path escapes project root")
    return resolved.relative_to(root.resolve()).as_posix()
