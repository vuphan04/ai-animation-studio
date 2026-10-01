"""Local Unreal Editor process adapter; never enabled by the P1 fake worker."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

from .domain import DomainError, canonical
from .p2 import validate_result


class UnrealAdapter:
    kind = "UNREAL_EDITOR_PYTHON"

    def __init__(self, editor: Path, project: Path, data_root: Path):
        self.editor = editor.resolve()
        self.project = project.resolve()
        self.data_root = data_root.resolve()
        self.script = Path(__file__).resolve().parents[3] / "tools" / "unreal" / "editor_agent.py"
        if not self.editor.is_file() or self.editor.name.lower() not in ("unrealeditor-cmd.exe", "unrealeditor.exe"):
            raise DomainError("complete Unreal Editor executable required")
        if not self.project.is_file() or self.project.suffix.lower() != ".uproject":
            raise DomainError("real .uproject required")
        try:
            descriptor = json.loads(self.project.read_text(encoding="utf-8-sig"))
            plugins = {p["Name"]: p.get("Enabled") for p in descriptor.get("Plugins", [])}
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise DomainError("Không đọc được tệp .uproject hợp lệ.") from exc
        if any(plugins.get(name) is not True for name in ("PythonScriptPlugin", "EditorScriptingUtilities", "SequencerScripting")):
            raise DomainError("Bật PythonScriptPlugin, EditorScriptingUtilities và SequencerScripting trong project Unreal.")

    def execute(self, operation: dict, mode: str) -> dict:
        if mode not in ("preflight", "apply", "observe"):
            raise DomainError("invalid Unreal agent mode")
        target = operation.get("target_project")
        if target and os.path.normcase(str(Path(target).resolve())) != os.path.normcase(str(self.project)):
            raise DomainError("Project Unreal đã đổi so với project gắn với thao tác; không chạy trên project khác.", 409)
        folder = self.data_root / "unreal-operations" / operation["operation_id"]
        folder.mkdir(parents=True, exist_ok=True)
        source = folder / "operation.json"
        source.write_text(canonical(operation), encoding="utf-8")
        result_path = folder / f"{mode}-{uuid.uuid4().hex}-result.json"
        env = os.environ.copy()
        env.update({"STUDIO_P2_OPERATION": str(source), "STUDIO_P2_RESULT": str(result_path),
                    "STUDIO_P2_DB": str(self.data_root / "studio.sqlite3"), "STUDIO_P2_MODE": mode})
        command = [str(self.editor), str(self.project), f"-ExecutePythonScript={self.script}"]
        completed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=1800, check=False)
        if not result_path.is_file():
            raise DomainError(f"Unreal did not produce an agent result; exit={completed.returncode}; outcome requires reconcile", 409)
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if result.get("status") == "BLOCKED":
            raise DomainError(f"Unreal agent blocked: {result.get('error', 'unknown')}", 409)
        if mode == "apply" and result.get("status") != "APPLIED":
            raise DomainError("Unreal apply result missing", 409)
        if mode == "observe" and result.get("status") != "OBSERVED":
            raise DomainError("Unreal observe result missing", 409)
        if mode != "preflight":
            validate_result(operation, result)
        if completed.returncode != 0:
            raise DomainError(f"Unreal exit={completed.returncode}; inspect actual output before retry", 409)
        return result
