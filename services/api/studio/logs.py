"""The app's logging: levels, files on disk, secret redaction and the recent history the launcher window shows.

Every module logs through `get(<module>)`, which names a logger `studio.<module>`. Nothing here is set up on import
except a null handler, so tests and tools that import the backend stay quiet; the launcher calls `setup`.
Log text is technical English; messages the app shows the operator (often Vietnamese) are quoted as they are.
"""

from __future__ import annotations

import io
import json
import logging
import os
import platform
import queue
import re
import sys
import threading
import time
import traceback
import zipfile
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from importlib import metadata
from pathlib import Path

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
FILE_MODES = ("DAY", "SESSION")
KEEP_DAYS = (1, 365)
# What the launcher remembers between runs. Stored in the data folder; holds no secrets.
DEFAULTS = {"language": "vi", "file_mode": "DAY", "keep_days": 30, "file_debug": False,
            "levels": ["INFO", "WARNING", "ERROR"], "errors_only": False, "autoscroll": True, "geometry": ""}
# Libraries that talk to providers log request details at DEBUG/INFO; only their problems are kept.
QUIET_LIBRARIES = ("anthropic", "httpx", "httpx2", "httpcore", "httpcore2", "openai", "urllib3", "asyncio", "multipart", "PIL")
FILE_PATTERN = re.compile(r"^studio-\d{4}-\d{2}-\d{2}(_\d{2}-\d{2}-\d{2})?\.log$")
SECRETS = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/\-]{8,}=*"),
    re.compile(r"(?i)((?:x-api-key|api[_-]?key|authorization|token|password|secret)[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]{6,}"),
)

logging.getLogger("studio").addHandler(logging.NullHandler())


def get(module: str) -> logging.Logger:
    return logging.getLogger("studio." + module)


def redact(text: str) -> str:
    for pattern in SECRETS:
        text = pattern.sub(lambda match: (match.group(1) if pattern.groups else "") + "[REDACTED]", text)
    return text


def tail(text: str | None, lines: int = 30) -> str:
    """The last non-empty lines a program printed, for a log entry about its failure."""
    rows = [row.rstrip() for row in (text or "").splitlines() if row.strip()]
    return "\n".join(rows[-lines:])


def level_name(levelno: int) -> str:
    return "ERROR" if levelno >= logging.ERROR else "WARNING" if levelno >= logging.WARNING else "INFO" if levelno >= logging.INFO else "DEBUG"


def module_name(logger_name: str) -> str:
    if logger_name.startswith("studio."):
        return logger_name[len("studio."):]
    if logger_name.startswith("uvicorn"):
        return "server"
    return logger_name.split(".")[0] or "root"


@dataclass(frozen=True)
class Entry:
    seq: int
    created: float
    level: str
    module: str
    message: str
    detail: str = ""

    def stamp(self, with_date: bool = True) -> str:
        moment = datetime.fromtimestamp(self.created)
        return moment.strftime("%Y-%m-%d %H:%M:%S" if with_date else "%H:%M:%S") + f".{int(moment.microsecond / 1000):03d}"

    def line(self) -> str:
        first, *more = self.message.splitlines() or [""]
        rest = more + (self.detail.splitlines() if self.detail else [])
        return f"{self.stamp()} | {self.level:<7} | {self.module} | {first}" + "".join("\n    " + row for row in rest)

    def summary(self) -> str:
        return self.message.splitlines()[0] if self.message else ""


_sequence = iter(range(1, 1 << 62))
_sequence_lock = threading.Lock()


def entry_from(record: logging.LogRecord) -> Entry:
    try:
        message = record.getMessage()
    except Exception:  # a bad format string must not lose the line
        message = f"{record.msg!r} {record.args!r}"
    detail = ""
    if record.exc_info:
        detail = "".join(traceback.format_exception(*record.exc_info)).rstrip()
    elif record.stack_info:
        detail = record.stack_info
    with _sequence_lock:
        seq = next(_sequence)
    return Entry(seq, record.created, level_name(record.levelno), module_name(record.name), redact(message), redact(detail))


def matches(entry: Entry, *, levels=LEVELS, errors_only: bool = False, module: str | None = None, text: str = "") -> bool:
    """The launcher's filter: errors-only wins over the level choice; the search looks at module, message and traceback."""
    if errors_only:
        if entry.level != "ERROR":
            return False
    elif entry.level not in levels:
        return False
    if module and entry.module != module and not entry.module.startswith(module + "."):
        return False
    needle = text.strip().casefold()
    return not needle or needle in entry.module.casefold() or needle in entry.message.casefold() or needle in entry.detail.casefold()


class MemoryLog(logging.Handler):
    """The most recent entries of this run, and a queue of new ones for the launcher window to pick up."""

    def __init__(self, capacity: int = 20000):
        super().__init__(logging.DEBUG)
        self._entries: deque[Entry] = deque(maxlen=capacity)
        self._new: queue.SimpleQueue[Entry] = queue.SimpleQueue()
        self._guard = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        entry = entry_from(record)
        with self._guard:
            self._entries.append(entry)
        self._new.put(entry)

    def entries(self) -> list[Entry]:
        with self._guard:
            return list(self._entries)

    def drain(self, limit: int = 2000) -> list[Entry]:
        taken = []
        while len(taken) < limit:
            try:
                taken.append(self._new.get_nowait())
            except queue.Empty:
                break
        return taken

    def clear(self) -> None:
        with self._guard:
            self._entries.clear()


class LogFile(logging.Handler):
    """Writes every entry to `<data>/logs`: one file per day (studio-2026-10-17.log) or one per launcher run
    (studio-2026-10-17_14-03-22.log). DEBUG lines, such as each successful HTTP request, are kept only when asked."""

    def __init__(self, folder: Path, mode: str = "DAY", file_debug: bool = False, *, started: datetime | None = None):
        super().__init__(logging.DEBUG)
        self.folder = Path(folder)
        self.started = started or datetime.now()
        self.mode, self.file_debug = mode, file_debug
        self._stream = None
        self._path: Path | None = None

    def configure(self, mode: str, file_debug: bool) -> None:
        self.acquire()
        try:
            self.mode, self.file_debug = mode, file_debug
            self._close_stream()
        finally:
            self.release()

    def path_for(self, moment: datetime) -> Path:
        name = moment.strftime("studio-%Y-%m-%d.log") if self.mode == "DAY" else self.started.strftime("studio-%Y-%m-%d_%H-%M-%S.log")
        return self.folder / name

    @property
    def path(self) -> Path:
        return self.path_for(datetime.now())

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.INFO and not self.file_debug:
            return
        try:
            target = self.path_for(datetime.fromtimestamp(record.created))
            if target != self._path:
                self._close_stream()
                self.folder.mkdir(parents=True, exist_ok=True)
                self._stream = target.open("a", encoding="utf-8", newline="\n")
                self._path = target
            self._stream.write(entry_from(record).line() + "\n")
            self._stream.flush()
        except Exception:
            self.handleError(record)

    def _close_stream(self) -> None:
        if self._stream:
            self._stream.close()
        self._stream, self._path = None, None

    def close(self) -> None:
        self.acquire()
        try:
            self._close_stream()
        finally:
            self.release()
        super().close()


class ConsoleLog(logging.StreamHandler):
    """The console of `launch_studio.py` without a window: INFO and above, so successful requests stay out."""

    def __init__(self, stream=None):
        super().__init__(stream or sys.stderr)
        self.setLevel(logging.INFO)

    def format(self, record: logging.LogRecord) -> str:
        return entry_from(record).line()


class StreamToLog(io.TextIOBase):
    """Stands in for stdout/stderr under pythonw.exe, where both are None, so stray prints become log lines."""

    def __init__(self, logger: logging.Logger, level: int):
        self.logger, self.level, self._buffer = logger, level, ""

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                self.logger.log(self.level, line.rstrip())
        return len(text)


def prune(folder: Path, keep_days: int, *, now: datetime | None = None) -> list[str]:
    """Removes the app's own log files older than `keep_days`; anything else in the folder is left alone."""
    limit = (now or datetime.now()) - timedelta(days=keep_days)
    removed = []
    for path in Path(folder).glob("studio-*.log") if Path(folder).is_dir() else []:
        if FILE_PATTERN.match(path.name) and datetime.fromtimestamp(path.stat().st_mtime) < limit:
            try:
                path.unlink()
                removed.append(path.name)
            except OSError:
                pass
    return removed


class Throttle:
    """Lets the same repeated error through once per `window` seconds and counts what it held back."""

    def __init__(self, window: float = 300.0, clock=time.monotonic):
        self.window, self.clock = window, clock
        self._seen: dict = {}

    def check(self, key) -> tuple[bool, int]:
        moment = self.clock()
        last = self._seen.get(key)
        if last is None or moment - last[0] >= self.window:
            self._seen[key] = [moment, 0]
            return True, last[1] if last else 0
        last[1] += 1
        return False, 0


class Settings:
    """The launcher's own settings in `<data>/launcher.json`, edited only through the launcher window."""

    def __init__(self, data_root: Path):
        self.path = Path(data_root) / "launcher.json"

    def load(self) -> dict:
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        value = dict(DEFAULTS)
        for key, default in DEFAULTS.items():
            if isinstance(saved, dict) and key in saved:
                try:
                    value.update(self._checked({key: saved[key]}))
                except ValueError:
                    value[key] = default
        return value

    @staticmethod
    def _checked(change: dict) -> dict:
        for key, item in change.items():
            if key not in DEFAULTS:
                raise ValueError(f"unknown setting {key}")
            if key == "language" and item not in ("vi", "en"):
                raise ValueError("language must be vi or en")
            if key == "file_mode" and item not in FILE_MODES:
                raise ValueError("file_mode must be DAY or SESSION")
            if key == "keep_days" and (type(item) is not int or not KEEP_DAYS[0] <= item <= KEEP_DAYS[1]):
                raise ValueError("keep_days must be a whole number from 1 to 365")
            if key in ("file_debug", "errors_only", "autoscroll") and type(item) is not bool:
                raise ValueError(f"{key} must be true or false")
            if key == "levels" and (not isinstance(item, list) or any(level not in LEVELS for level in item)):
                raise ValueError("levels must list DEBUG, INFO, WARNING or ERROR")
            if key == "geometry" and (not isinstance(item, str) or len(item) > 40):
                raise ValueError("geometry must be a short text")
        return change

    def save(self, **change) -> dict:
        value = {**self.load(), **self._checked(change)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)
        return value


class LogSystem:
    """What `setup` installed: the file writer, the history for the window and the folder they use."""

    def __init__(self, folder: Path, file: LogFile, memory: MemoryLog, handlers: list[logging.Handler]):
        self.folder, self.file, self.memory, self._handlers = folder, file, memory, handlers

    def apply(self, settings: dict) -> list[str]:
        """Takes new file settings at once and removes files past the new limit."""
        self.file.configure(settings["file_mode"], settings["file_debug"])
        return prune(self.folder, settings["keep_days"])

    def close(self) -> None:
        root = logging.getLogger()
        for handler in self._handlers:
            root.removeHandler(handler)
            handler.close()


def setup(data_root: Path, settings: dict, *, console: bool = False, capacity: int = 20000) -> LogSystem:
    """Routes the whole process's logging to the log folder and the launcher's history."""
    folder = Path(data_root) / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    file = LogFile(folder, settings["file_mode"], settings["file_debug"])
    memory = MemoryLog(capacity)
    handlers: list[logging.Handler] = [file, memory] + ([ConsoleLog()] if console else [])
    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    for handler in handlers:
        root.addHandler(handler)
    logging.getLogger("studio").setLevel(logging.DEBUG)
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).setLevel(logging.INFO)
    # The app writes its own line per request (studio.http), with the reason for every refusal.
    logging.getLogger("uvicorn.access").disabled = True
    for name in QUIET_LIBRARIES:
        logging.getLogger(name).setLevel(logging.WARNING)
    logging.captureWarnings(True)
    crash = get("launcher")
    threading.excepthook = lambda args: crash.error("Unhandled error in thread %s", args.thread.name if args.thread else "?",
                                                    exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    prune(folder, settings["keep_days"])
    return LogSystem(folder, file, memory, handlers)


def mask_home(text: str) -> str:
    home = str(Path.home())
    return text.replace(home, "%USERPROFILE%").replace(home.replace("\\", "/"), "%USERPROFILE%")


def system_info(data_root: Path, build_id: str, extra: dict | None = None) -> dict:
    packages = {}
    for name in ("fastapi", "uvicorn", "starlette", "pydantic", "anthropic", "httpx", "httpx2", "keyring", "pywin32"):
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    return {"app": "ai-animation-studio", "build_id": build_id, "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "python": sys.version.split()[0], "executable": mask_home(sys.executable), "platform": platform.platform(),
            "data_root": mask_home(str(Path(data_root).resolve())), "packages": packages, **(extra or {})}


README = """AI Animation Studio - log export / gói log để sửa lỗi

current-view.log   Dòng đang hiển thị trong launcher (theo bộ lọc lúc xuất) / lines shown in the launcher with its filter
session.log        Mọi dòng của lần chạy này, kể cả DEBUG / every line of this run, DEBUG included
files/             File log trong thư mục data/logs của vài ngày gần đây / recent files from data/logs
system-info.json   Phiên bản app, Python, thư viện, cài đặt log / app, Python and library versions, log settings

Khóa API không bao giờ được ghi vào log; chuỗi giống khóa bị thay bằng [REDACTED]. Đường dẫn thư mục người dùng
được thay bằng %USERPROFILE%. / API keys are never logged; key-like text is replaced by [REDACTED] and the user
folder by %USERPROFILE%.
"""


def export(target: Path, *, folder: Path, session: list[Entry], view: list[Entry], info: dict, days: int = 3) -> Path:
    """A zip for whoever fixes the problem: the visible lines, the whole run, recent log files and system details."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    since = datetime.now() - timedelta(days=days)
    recent = sorted(path for path in Path(folder).glob("studio-*.log")
                    if FILE_PATTERN.match(path.name) and datetime.fromtimestamp(path.stat().st_mtime) >= since) if Path(folder).is_dir() else []
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.txt", README)
        archive.writestr("current-view.log", mask_home("\n".join(entry.line() for entry in view) + "\n"))
        archive.writestr("session.log", mask_home("\n".join(entry.line() for entry in session) + "\n"))
        for path in recent:
            archive.writestr(f"files/{path.name}", mask_home(redact(path.read_text(encoding="utf-8", errors="replace"))))
        archive.writestr("system-info.json", json.dumps(info, ensure_ascii=False, indent=2))
    return target
