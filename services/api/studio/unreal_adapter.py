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
