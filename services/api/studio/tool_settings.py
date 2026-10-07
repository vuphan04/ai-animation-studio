"""Operator-selected external tools. Saved means the binary answered `-version`, not that a render works."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from .domain import DomainError
from .service import now


def probe_ffmpeg(path: str) -> dict:
    file = Path(path.strip()) if isinstance(path, str) else None
    if not file or not path.strip() or not file.is_file() or file.name.lower() != "ffmpeg.exe":
        raise DomainError("Chọn tệp ffmpeg.exe có trên máy này.")
    try:
        completed = subprocess.run([str(file), "-version"], capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DomainError("Không chạy được ffmpeg.exe đã chọn; kiểm tra tệp và quyền thực thi.") from exc
    match = re.match(r"ffmpeg version (\S+)", completed.stdout or "")
    if completed.returncode != 0 or not match:
        raise DomainError("Tệp đã chọn không trả lời như FFmpeg; chọn đúng ffmpeg.exe.")
    probe = file.with_name("ffprobe.exe")
    return {"ffmpeg_path": str(file.resolve()), "version": match.group(1),
            "ffprobe_path": str(probe.resolve()) if probe.is_file() else None}


class ToolSettings:
    def __init__(self, studio):
        self.studio = studio
        self.db = studio.db

    def get(self) -> dict:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM tool_settings WHERE id=1").fetchone()
        result = {"revision": row["revision"] if row else 0, "ffmpeg_path": row["ffmpeg_path"] if row else "",
                  "configured": False, "version": None, "ffprobe_path": None, "error": None,
                  "detected_path": shutil.which("ffmpeg") or ""}
        if row:
            try:
                result.update(probe_ffmpeg(row["ffmpeg_path"]), configured=True)
            except DomainError as exc:
                result["error"] = str(exc)
        return result

    def save(self, expected_revision: int, ffmpeg_path: str) -> dict:
        valid = probe_ffmpeg(ffmpeg_path)
        if not valid["ffprobe_path"]:
            raise DomainError("Không thấy ffprobe.exe cạnh ffmpeg.exe; chọn bản FFmpeg đầy đủ (có ffprobe) để kiểm tra media.")
        with self.db.transaction() as conn:
            row = conn.execute("SELECT revision FROM tool_settings WHERE id=1").fetchone()
            if (row["revision"] if row else 0) != expected_revision:
                raise DomainError("Cấu hình công cụ đã đổi ở phiên khác; tải lại trước khi lưu.", 409)
            conn.execute("INSERT OR REPLACE INTO tool_settings(id,revision,ffmpeg_path,updated_at) VALUES(1,?,?,?)",
                         (expected_revision + 1, valid["ffmpeg_path"], now()))
            self.studio.audit(conn, None, "settings.tools.changed", "ffmpeg", {"revision": expected_revision + 1, "version": valid["version"]})
        return self.get()
