"""Local Unreal Editor process adapter: runs the editor scripts in tools/unreal and reads the result file each one writes."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

from . import logs
from .domain import DomainError, canonical


unreal_log = logs.get("unreal")
REQUIRED_PLUGINS = ("PythonScriptPlugin", "EditorScriptingUtilities", "SequencerScripting")
_found_plugins: dict[tuple, dict[str, dict]] = {}
# A JSON string, or a comma that only precedes a closing bracket.
TRAILING_COMMA = re.compile(r'"(?:\\.|[^"\\])*"|,(\s*[}\]])')


def read_descriptor(path: Path):
    """A .uproject or .uplugin file as Unreal reads it: Unreal accepts a comma before } or ], and its own engine
    plugins use that (34 of the 895 in UE 5.8, SequencerScripting among them), so strict JSON is not enough."""
    text = path.read_text(encoding="utf-8-sig")
    return json.loads(TRAILING_COMMA.sub(lambda match: match.group(1) if match.group(1) is not None else match.group(0), text))


def find_plugins(folder: Path, *, keep: bool = False) -> dict[str, dict]:
    """The .uplugin descriptors under a Plugins folder, by lower-case name, searched the way Unreal does.

    A folder that holds a .uplugin is a plugin and is not searched further. With `keep` the result is reused while
    the app runs: an engine has about a thousand plugins and they do not change under a running app, while a
    project's few plugins are read again each time.
    """
    try:
        key = (str(folder), folder.stat().st_mtime_ns)
    except OSError:
        return {}
    if keep and key in _found_plugins:
        return _found_plugins[key]
    found: dict[str, dict] = {}
    pending = [folder]
    while pending:
        try:
            items = list(os.scandir(pending.pop()))
        except OSError:
            continue
        descriptors = [item for item in items if item.name.lower().endswith(".uplugin") and item.is_file()]
        for item in descriptors:
            try:
                descriptor = read_descriptor(Path(item.path))
            except (OSError, ValueError):
                continue
            if isinstance(descriptor, dict):
                found.setdefault(item.name[:-len(".uplugin")].casefold(), descriptor)
        if not descriptors:
            pending.extend(Path(item.path) for item in items if item.is_dir() and not item.name.startswith("."))
    if keep:
        _found_plugins[key] = found
    return found


def engine_folder(editor: Path) -> Path | None:
    """<Engine> for <Engine>/Binaries/Win64/UnrealEditor-Cmd.exe; None for any other layout."""
    if editor.parent.name.lower() == "win64" and editor.parent.parent.name.lower() == "binaries":
        engine = editor.parents[2]
        if (engine / "Plugins").is_dir():
            return engine
    return None


def enabled_plugins(descriptor: dict, explicit: dict[str, bool | None], engine: dict[str, dict], project: dict[str, dict]) -> set[str]:
    """Lower-case names of the plugins Unreal loads for this project.

    Unreal writes a plugin into the .uproject only when the project changes it from the default. A plugin that is
    enabled by default, or that another enabled plugin depends on, shows as ticked in Edit -> Plugins without an
    entry, so the .uproject alone is not enough.
    """
    enabled = {name for name, on in explicit.items() if on is True}
    disabled = {name for name, on in explicit.items() if on is False}
    engine_defaults = descriptor.get("DisableEnginePluginsByDefault") is not True
    for name, plugin in {**engine, **project}.items():
        if explicit.get(name) is not None:
            continue
        default = plugin.get("EnabledByDefault")
        # A project's own plugin is on unless it says otherwise; an engine plugin only when it says so.
        if (name in project and default is not False) or (name not in project and default is True and engine_defaults):
            enabled.add(name)
    available = {**engine, **project}
    pending = list(enabled)
    while pending:
        dependencies = available.get(pending.pop(), {}).get("Plugins")
        for dependency in dependencies if isinstance(dependencies, list) else []:
            if isinstance(dependency, dict) and dependency.get("Enabled") is True and isinstance(dependency.get("Name"), str):
                name = dependency["Name"].casefold()
                if name not in enabled and name not in disabled:
                    enabled.add(name)
                    pending.append(name)
    return enabled


class UnrealAdapter:
    kind = "UNREAL_EDITOR_PYTHON"

    def __init__(self, editor: Path, project: Path, data_root: Path):
        self.editor = editor.resolve()
        self.project = project.resolve()
        self.data_root = data_root.resolve()
        self.scripts = Path(__file__).resolve().parents[3] / "tools" / "unreal"
        if not self.editor.is_file():
            raise DomainError("Không tìm thấy tệp Unreal Editor trên máy này. Chọn UnrealEditor-Cmd.exe hoặc UnrealEditor.exe trong bản cài Unreal đầy đủ.")
        if self.editor.name.lower() not in ("unrealeditor-cmd.exe", "unrealeditor.exe"):
            raise DomainError("Sai tệp Unreal Editor. Chọn UnrealEditor-Cmd.exe hoặc UnrealEditor.exe; không chọn launcher hoặc shortcut.")
        if not self.project.is_file() or self.project.suffix.lower() != ".uproject":
            raise DomainError("Không tìm thấy project Unreal hợp lệ trên máy này. Chọn tệp .uproject, không chọn thư mục project.")
        try:
            descriptor = read_descriptor(self.project)
        except (ValueError, OSError) as exc:
            raise DomainError("Không đọc được tệp .uproject. Tệp phải là JSON UTF-8 hợp lệ và có quyền đọc.") from exc
        if not isinstance(descriptor, dict):
            raise DomainError("Tệp .uproject không hợp lệ: nội dung JSON phải là một đối tượng, không phải danh sách hoặc null.")
        entries = descriptor.get("Plugins", [])
        if not isinstance(entries, list):
            raise DomainError("Tệp .uproject không hợp lệ: Plugins phải là một danh sách.")
        plugins = {}
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("Name"), str) or not entry["Name"].strip():
                raise DomainError("Tệp .uproject không hợp lệ: mỗi plugin phải có Name là tên không rỗng.")
            name = entry["Name"]
            if name in plugins or ("Enabled" in entry and not isinstance(entry["Enabled"], bool)):
                raise DomainError(f"Tệp .uproject không hợp lệ: plugin {name} bị trùng hoặc Enabled không phải true/false.")
            plugins[name] = entry.get("Enabled")
        explicit = {name.casefold(): on for name, on in plugins.items()}
        engine = engine_folder(self.editor)
        engine_plugins = find_plugins(engine / "Plugins", keep=True) if engine else {}
        project_plugins = find_plugins(self.project.parent / "Plugins")
        enabled = enabled_plugins(descriptor, explicit, engine_plugins, project_plugins)
        missing = [name for name in REQUIRED_PLUGINS if name.casefold() not in enabled]
        if missing:
            def why(name: str) -> str:
                key = name.casefold()
                listed = "not listed" if key not in explicit else "disabled" if explicit[key] is False else "listed without Enabled"
                absent = engine and key not in engine_plugins and key not in project_plugins
                return f"{name} ({listed} in the .uproject{', not installed in this engine' if absent else ''})"

            # Lands in the session log that Export Logs collects, for someone helping from another PC.
            unreal_log.debug("Plugins missing in %s: %s. Engine folder: %s (%d plugins), %d project plugins", self.project,
                             ", ".join(why(name) for name in missing), engine or "not found", len(engine_plugins), len(project_plugins))
            raise DomainError("Project chưa bật plugin bắt buộc: " + ", ".join(missing) +
                              ". Mở project trong Unreal → Edit → Plugins, bật các plugin này, lưu project rồi thử lại. Tệp .uproject đã kiểm tra: " + str(self.project))

    def _run_script(self, script: str, prefix: str, folder: Path, request: dict, label: str) -> dict:
        """Run an editor script that reports through a result file."""
        folder.mkdir(parents=True, exist_ok=True)
        source = folder / "request.json"
        source.write_text(canonical(request), encoding="utf-8")
        result_path = folder / f"{uuid.uuid4().hex}-result.json"
        env = os.environ.copy()
        env.update({f"STUDIO_{prefix}_REQUEST": str(source), f"STUDIO_{prefix}_RESULT": str(result_path)})
        # Unreal reads backslash escapes in this path ("\tools" became a tab and the script was not found), so it
        # gets forward slashes. -unattended stops a dialog from holding the editor open until the timeout.
        command = [str(self.editor), str(self.project), f"-ExecutePythonScript={(self.scripts / script).as_posix()}", "-unattended", "-nosplash"]
        unreal_log.info("Running %s in %s (request %s)", script, self.project.name, source)
        started = time.monotonic()
        try:
            completed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=1800, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            unreal_log.error("%s could not run: %s: %s", script, type(exc).__name__, exc)
            raise
        if not result_path.is_file():
            unreal_log.error("%s exited %d after %.0f s without a result file\n%s", script, completed.returncode, time.monotonic() - started,
                             logs.tail((completed.stdout or "") + "\n" + (completed.stderr or "")))
            raise DomainError(f"Unreal không trả kết quả {label}; exit={completed.returncode}. Kiểm tra project mở được trong Unreal 5.8.", 409)
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if result.get("status") == "BLOCKED":
            unreal_log.warning("%s blocked after %.0f s: %s", script, time.monotonic() - started, result.get("error", "-"))
            raise DomainError(f"Unreal chặn thao tác {label}: {result.get('error', 'không rõ')}", 409)
        unreal_log.info("%s finished in %.0f s (exit %d)", script, time.monotonic() - started, completed.returncode)
        return result

    def scan_assets(self, request: dict) -> dict:
        """Read-only Asset Registry inventory; the script never loads or saves an asset."""
        return self._run_script("asset_scan.py", "SCAN", self.data_root / "unreal-scans" / request["operation_id"], request, "quét asset")

    def capture_references(self, request: dict) -> dict:
        """Style bible reference frames; spawned actors are transient and nothing is saved."""
        return self._run_script("style_capture.py", "CAPTURE", self.data_root / "unreal-captures" / request["operation_id"], request, "chụp reference")

    def build_episode(self, request: dict) -> dict:
        """Workflow V4.5 steps 07 and 08-b6. Unlike the scan and capture scripts, this one creates and saves assets."""
        return self._run_script("episode_build.py", "BUILD", self.data_root / "unreal-builds" / request["operation_id"], request, "dựng tập")

    def prepare_render(self, request: dict) -> dict:
        """Workflow V4.5 steps 09 and 10: inspect the shots and write a Movie Render Queue manifest. Renders nothing."""
        return self._run_script("episode_render.py", "RENDER", self.data_root / "unreal-renders" / request["operation_id"], request, "chuẩn bị render")

    def render_queue(self, manifest: str, timeout: int = 14400) -> None:
        """Render a prepared Movie Render Queue manifest in a second, game-mode Unreal process and wait for it to exit."""
        saved = self.project.parent / "Saved"
        path = Path(manifest)
        argument = path.resolve().relative_to(saved.resolve()).as_posix() if path.is_absolute() and path.resolve().is_relative_to(saved.resolve()) else manifest
        command = [str(self.editor), str(self.project), "MoviePipelineEntryMap?game=/Script/MovieRenderPipelineCore.MoviePipelineGameMode", "-game",
                   f"-MoviePipelineConfig={argument}", "-windowed", "-log", "-unattended", "-nosplash", "-RenderOffscreen"]
        unreal_log.info("Rendering Movie Render Queue manifest %s", argument)
        started = time.monotonic()
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        if completed.returncode != 0:
            unreal_log.error("Render exited %d after %.0f s\n%s", completed.returncode, time.monotonic() - started,
                             logs.tail((completed.stdout or "") + "\n" + (completed.stderr or "")))
            raise DomainError(f"Unreal thoát với mã {completed.returncode} khi render; xem log trong thư mục Saved/Logs của project Unreal.", 409)
