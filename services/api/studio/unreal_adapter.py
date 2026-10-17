"""Local Unreal Editor process adapter: runs the editor scripts in tools/unreal and reads the result file each one writes."""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path

from . import logs
from .domain import DomainError, canonical


unreal_log = logs.get("unreal")


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
            descriptor = json.loads(self.project.read_text(encoding="utf-8-sig"))
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
        missing = [name for name in ("PythonScriptPlugin", "EditorScriptingUtilities", "SequencerScripting")
                   if plugins.get(name) is not True]
        if missing:
            raise DomainError("Project chưa bật plugin bắt buộc: " + ", ".join(missing) +
                              ". Mở project trong Unreal → Edit → Plugins, bật các plugin này, lưu project rồi thử lại.")

    def _run_script(self, script: str, prefix: str, folder: Path, request: dict, label: str) -> dict:
        """Run an editor script that reports through a result file."""
        folder.mkdir(parents=True, exist_ok=True)
        source = folder / "request.json"
        source.write_text(canonical(request), encoding="utf-8")
        result_path = folder / f"{uuid.uuid4().hex}-result.json"
        env = os.environ.copy()
        env.update({f"STUDIO_{prefix}_REQUEST": str(source), f"STUDIO_{prefix}_RESULT": str(result_path)})
        command = [str(self.editor), str(self.project), f"-ExecutePythonScript={self.scripts / script}"]
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
