"""Interactive Windows file chooser for the local, same-PC Studio server."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from .domain import DomainError


_picker_lock = threading.Lock()
# Kinds added by workflow V4.5 zone C: window title and file filter.
FILE_KINDS = {
    "audio": ("Chọn file âm thanh (SFX, ambience, nhạc hoặc Foley)", [("Âm thanh", "*.wav *.mp3 *.flac *.m4a *.ogg *.aif *.aiff")]),
    "python": ("Chọn python.exe của môi trường đã cài HunyuanVideo-Foley", [("Python", "python*.exe")]),
    "foley_script": ("Chọn infer.py trong thư mục HunyuanVideo-Foley", [("Python script", "*.py")]),
    "foley_config": ("Chọn file cấu hình model (.yaml)", [("YAML", "*.yaml *.yml")]),
}


def pick_file(kind: str, initial_path: str = "") -> str | None:
    if kind not in FILE_KINDS and kind not in ("editor", "project", "ffmpeg", "image", "control", "video"):
        raise DomainError("Loại tệp cần chọn không hợp lệ.")
    if sys.platform != "win32":
        raise DomainError("Hộp thoại chọn tệp chỉ hỗ trợ Windows; bạn có thể dán đường dẫn vào ô cấu hình.", 409)
    if not _picker_lock.acquire(blocking=False):
        raise DomainError("Đang mở một hộp thoại chọn tệp khác.", 409)
    try:
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
        except Exception as exc:
            raise DomainError("Không mở được hộp thoại chọn tệp; hãy dán đường dẫn vào ô cấu hình.", 409) from exc
        try:
            root.withdraw()
            root.attributes("-topmost", True)
            parent = Path(initial_path).expanduser().parent if initial_path else Path.home()
            initial_dir = str(parent) if parent.is_dir() else str(Path.home())
            if kind in FILE_KINDS:
                title, types = FILE_KINDS[kind]
                selected = filedialog.askopenfilename(parent=root, title=title, initialdir=initial_dir, filetypes=types)
            elif kind == "image":
                selected = filedialog.askopenfilename(
                    parent=root, title="Chọn ảnh tham chiếu",
                    initialdir=initial_dir, filetypes=[("Ảnh PNG/JPEG", "*.png *.jpg *.jpeg")],
                )
            elif kind == "control":
                selected = filedialog.askopenfilename(
                    parent=root, title="Chọn video nguồn UE hoặc frame đầu của shot Hybrid",
                    initialdir=initial_dir, filetypes=[("Video hoặc ảnh", "*.mp4 *.mov *.png *.jpg *.jpeg")],
                )
            elif kind == "video":
                selected = filedialog.askopenfilename(
                    parent=root, title="Chọn file video đã render hoặc đã tạo ngoài app",
                    initialdir=initial_dir, filetypes=[("Video", "*.mp4 *.mov")],
                )
            elif kind == "ffmpeg":
                selected = filedialog.askopenfilename(
                    parent=root, title="Chọn ffmpeg.exe",
                    initialdir=initial_dir, filetypes=[("FFmpeg", "ffmpeg.exe")],
                )
            elif kind == "editor":
                selected = filedialog.askopenfilename(
                    parent=root, title="Chọn UnrealEditor-Cmd.exe",
                    initialdir=initial_dir, filetypes=[("Tệp thực thi", "*.exe")],
                )
            else:
                selected = filedialog.askopenfilename(
                    parent=root, title="Chọn project Unreal (.uproject)",
                    initialdir=initial_dir, filetypes=[("Unreal project", "*.uproject")],
                )
            return selected or None
        finally:
            root.destroy()
    finally:
        _picker_lock.release()


def pick_project_output(project_root: Path, kind: str) -> str | None:
    if kind not in ("cache_file", "render_folder", "reference_image", "video_file", "audio_stem", "evidence_file"):
        raise DomainError("Loại đầu ra cần chọn không hợp lệ.")
    if sys.platform != "win32":
        raise DomainError("Hộp thoại chọn đầu ra chỉ hỗ trợ Windows.", 409)
    if not _picker_lock.acquire(blocking=False):
        raise DomainError("Đang mở một hộp thoại chọn tệp khác.", 409)
    try:
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
        except Exception as exc:
            raise DomainError("Không mở được hộp thoại chọn đầu ra.", 409) from exc
        try:
            root.withdraw()
            root.attributes("-topmost", True)
            if kind == "render_folder":
                chosen = filedialog.askdirectory(parent=root, title="Chọn thư mục frame render", initialdir=str(project_root))
            else:
                filters = {"reference_image": [("Ảnh tham chiếu", "*.png *.jpg *.jpeg")],
                           "video_file": [("Video", "*.mp4 *.mov")],
                           "audio_stem": [("WAV PCM", "*.wav")]}.get(kind, [("Tất cả tệp", "*.*")])
                chosen = filedialog.askopenfilename(parent=root, title="Chọn media trong dự án", initialdir=str(project_root), filetypes=filters)
            if not chosen:
                return None
            path = Path(chosen).resolve()
            if not path.is_relative_to(project_root.resolve()):
                raise DomainError("Đầu ra phải nằm trong thư mục dự án Studio.", 409)
            return path.relative_to(project_root.resolve()).as_posix()
        finally:
            root.destroy()
    finally:
        _picker_lock.release()


def pick_folder(title: str = 'Chọn thư mục sao lưu/phục hồi') -> str | None:
    if sys.platform != 'win32':
        raise DomainError('Hộp thoại thư mục chỉ hỗ trợ Windows; có thể nhập đường dẫn trong app.', 409)
    if not _picker_lock.acquire(blocking=False):
        raise DomainError('Đang có hộp thoại khác.', 409)
    try:
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
        except Exception as exc:
            raise DomainError('Không mở được hộp thoại; nhập đường dẫn thư mục trong app.', 409) from exc
        try:
            root.withdraw()
            root.attributes('-topmost', True)
            return filedialog.askdirectory(parent=root, title=title) or None
        finally:
            root.destroy()
    finally:
        _picker_lock.release()
