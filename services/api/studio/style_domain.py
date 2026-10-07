"""Pure Style bible rules: fixed style phrases, banned words, prompt assembly and image file probing."""

from __future__ import annotations

import re
import struct
from pathlib import Path

from .domain import DomainError
from .series_domain import fold


CHARACTER_ROLES = ("turnaround_front", "turnaround_three_quarter", "turnaround_side", "turnaround_back", "concept")
ENVIRONMENT_ROLES = ("frame_01", "frame_02", "frame_03", "frame_04", "concept")
ROLES = {"character": CHARACTER_ROLES, "environment": ENVIRONMENT_ROLES}
MAX_IMAGE_BYTES = 50 * 1024 * 1024


def clean_terms(values: object, *, limit: int, max_length: int, label: str) -> list[str]:
    if not isinstance(values, list) or len(values) > limit or any(not isinstance(v, str) for v in values):
        raise DomainError(f"{label}: cần danh sách tối đa {limit} mục.")
    result, seen = [], set()
    for value in values:
        text = re.sub(r"\s+", " ", value).strip()
        if not text:
            continue
        if len(text) > max_length:
            raise DomainError(f"{label}: mỗi mục tối đa {max_length} ký tự.")
        if text.casefold() not in seen:
            seen.add(text.casefold())
            result.append(text)
    return result


def banned_hits(text: str, banned: list[str]) -> list[str]:
    """Banned words found in `text`, compared without case or diacritics and only on whole words."""
    haystack = fold(text or "")
    return [word for word in banned
            if fold(word) and re.search(r"(?<![a-z0-9])" + re.escape(fold(word)) + r"(?![a-z0-9])", haystack)]


def build_prompt(description: str, style_phrases: list[str]) -> str:
    """The fixed style phrases are always appended by the app, never left to a model to remember."""
    return ", ".join(part for part in [description.strip(), *style_phrases] if part)


def probe_image(path: Path) -> tuple[str, int | None, int | None]:
    """PNG or JPEG only; returns (format, width, height) from the file header."""
    size = path.stat().st_size
    if size <= 0 or size > MAX_IMAGE_BYTES:
        raise DomainError("Ảnh tham chiếu rỗng hoặc lớn hơn 50 MB.")
    with path.open("rb") as stream:
        head = stream.read(32)
        if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
            width, height = struct.unpack(">II", head[16:24])
            return "png", width, height
        if head[:2] == b"\xff\xd8":
            stream.seek(2)
            while True:
                marker = stream.read(4)
                if len(marker) < 4 or marker[0] != 0xFF:
                    return "jpg", None, None
                length = struct.unpack(">H", marker[2:4])[0]
                if 0xC0 <= marker[1] <= 0xCF and marker[1] not in (0xC4, 0xC8, 0xCC):
                    data = stream.read(5)
                    if len(data) < 5:
                        return "jpg", None, None
                    height, width = struct.unpack(">HH", data[1:5])
                    return "jpg", width, height
                stream.seek(length - 2, 1)
    raise DomainError("Ảnh tham chiếu phải là PNG hoặc JPEG.")
