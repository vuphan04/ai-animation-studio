"""Claude through the Claude Code program installed on this machine.

The app never asks for, sees or stores a Claude account. It starts the local `claude` program, which uses whatever
sign-in its owner set up inside Claude Code, and reads back JSON. A screenplay is untrusted text, so every request runs
with all tools turned off, none of the operator's Claude Code customisations loaded, and an empty working folder.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import logs
from .domain import DomainError, canonical
from .service import now

# `default` leaves the choice to Claude Code, which knows what the signed-in plan may use.
MODELS = {"opus": "Claude Opus", "sonnet": "Claude Sonnet", "haiku": "Claude Haiku", "default": "Theo mặc định của Claude Code"}
PROMPT = "Read the JSON document on standard input and answer with the structured output only."
NOTE = "Dùng Claude Code trên máy này. Lượt dùng tính vào tài khoản đang đăng nhập trong Claude Code và không ghi vào Sổ chi phí của app."
MISSING = "Chưa tìm thấy Claude Code trên máy này; vào Cài đặt → Dịch vụ AI để kiểm tra."
TEST_SCHEMA = {"type": "object", "properties": {"ready": {"type": "boolean"}}, "required": ["ready"], "additionalProperties": False}
NAMES = ("claude.exe", "claude") if sys.platform == "win32" else ("claude",)
log = logs.get("claude_code")


class ClaudeCodeError(DomainError):
    """Claude Code could not be started or did not return a usable answer. Nothing is charged by the app."""

    def __init__(self, message: str):
        super().__init__(message, 409)


def _version_key(path: Path) -> tuple:
    return tuple(int(part) for part in re.findall(r"\d+", path.parts[-4] if len(path.parts) >= 4 else ""))


def detect() -> str:
    """The Claude Code executable this machine most likely uses, or an empty string."""
    found = shutil.which("claude")
    if found and Path(found).name.lower() in NAMES:
        return str(Path(found))
    home = Path.home()
    native = [home / ".local" / "bin" / name for name in NAMES]
    # The VS Code extension ships the same program; the newest installed version wins.
    bundled = sorted((path for folder in (".vscode", ".vscode-insiders") for path in (home / folder / "extensions").glob("anthropic.claude-code-*/resources/native-binary/claude*")
                      if path.name.lower() in NAMES), key=_version_key, reverse=True)
    return next((str(path) for path in native + bundled if path.is_file()), "")


class ClaudeCode:
    """One Claude Code executable: its version, its sign-in state and a single tool-less structured request."""

    def __init__(self, executable: str, *, run=subprocess.run):
        self.executable = executable
        self._run = run

    def _call(self, args: list[str], *, text: str | None = None, timeout: int = 30, cwd: str | None = None):
        if not self.executable or not Path(self.executable).is_file() or Path(self.executable).name.lower() not in NAMES:
            raise ClaudeCodeError("Không tìm thấy Claude Code trên máy này. Cài Claude Code, hoặc chọn tệp claude.exe trong Cài đặt → Dịch vụ AI.")
        try:
            return self._run([self.executable, *args], input=text, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                             cwd=cwd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as exc:
            raise ClaudeCodeError(f"Claude Code không trả lời sau {timeout // 60 or 1} phút; yêu cầu đã dừng.") from exc
        except OSError as exc:
            raise ClaudeCodeError("Không chạy được Claude Code; kiểm tra tệp đã chọn trong Cài đặt → Dịch vụ AI.") from exc

    def version(self) -> str:
        done = self._call(["--version"])
        if done.returncode != 0 or "Claude Code" not in done.stdout:
            raise ClaudeCodeError("Tệp đã chọn không trả lời như Claude Code; chọn đúng claude.exe.")
        return done.stdout.strip().split()[0]

    def auth(self) -> dict:
        """Sign-in state as Claude Code reports it. The account's e-mail and organisation are read and dropped here."""
        done = self._call(["auth", "status"])
        try:
            value = json.loads(done.stdout)
        except ValueError:
            value = {}
        return {"logged_in": bool(value.get("loggedIn")) and done.returncode == 0, "auth_method": str(value.get("authMethod") or "none"),
                "subscription_type": value.get("subscriptionType") if isinstance(value.get("subscriptionType"), str) else None}

    def ask(self, *, instructions: str, payload: dict, schema: dict, model: str, timeout: int) -> dict:
        """One request answered as JSON that matches `schema`. Claude gets no tools and no settings of the operator."""
        args = ["-p", PROMPT, "--output-format", "json", "--json-schema", canonical(schema), "--system-prompt", instructions,
                "--tools", "", "--disallowedTools", "mcp__*", "--strict-mcp-config", "--safe-mode", "--no-session-persistence",
                "--permission-mode", "dontAsk", "--max-turns", "8"]
        if model != "default":
            args += ["--model", model]
        log.info("Running Claude Code (model %s, time limit %d s)", model, timeout)
        started = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(prefix="studio-claude-") as folder:
                done = self._call(args, text=canonical(payload), timeout=timeout, cwd=folder)
        except ClaudeCodeError as exc:
            log.warning("Claude Code did not run: %s", exc)
            raise
        try:
            value = json.loads(done.stdout)
            if not isinstance(value, dict):
                raise ValueError("not an object")
        except ValueError as exc:
            detail = (done.stderr or done.stdout or "").strip().splitlines()
            log.error("Claude Code exited %d without readable JSON after %.1f s\n%s", done.returncode, time.monotonic() - started, logs.tail(done.stderr or done.stdout))
            raise ClaudeCodeError("Claude Code không trả về kết quả đọc được" + (f": {detail[-1][:300]}" if detail else ".")) from exc
        output = value.get("structured_output")
        if done.returncode != 0 or value.get("is_error") or not isinstance(output, dict):
            reason = value.get("result") if isinstance(value.get("result"), str) and value.get("result").strip() else str(value.get("subtype") or "không có kết quả có cấu trúc")
            log.warning("Claude Code answered without a structured result after %.1f s (exit %d): %s", time.monotonic() - started, done.returncode, reason.strip()[:300])
            raise ClaudeCodeError(f"Claude Code báo lỗi: {reason.strip()[:400]}")
        # Claude Code also makes small housekeeping calls on another model; the one that wrote the most is the one that answered.
        used = {name: usage for name, usage in (value.get("modelUsage") or {}).items() if isinstance(usage, dict)} if isinstance(value.get("modelUsage"), dict) else {}
        answered = max(used, key=lambda name: used[name].get("outputTokens") or 0, default=None)
        log.info("Claude Code answered in %.1f s with %s, session %s", time.monotonic() - started, answered or "an unreported model", value.get("session_id") or "-")
        return {"output": output, "session_id": str(value.get("session_id") or ""), "model": answered}


class ClaudeCodeSettings:
    """Which Claude Code executable and model the app uses. Persisted; holds no account data."""

    def __init__(self, studio, *, program=ClaudeCode, finder=detect):
        self.db = studio.db
        self.program = program
        self.finder = finder

    def _row(self) -> dict:
        with self.db.connect() as conn:
            return dict(conn.execute("SELECT revision,executable_path,model,verified_at,verified_model,updated_at FROM claude_code_settings WHERE id=1").fetchone())

    def executable(self) -> str:
        """The saved executable, or the one found on this machine when none was chosen."""
        return self._row()["executable_path"] or self.finder()

    def available(self) -> bool:
        path = self.executable()
        return bool(path) and Path(path).is_file()

    def client(self) -> ClaudeCode:
        return self.program(self.executable())

    def get(self) -> dict:
        value = self._row()
        value["detected_path"] = self.finder()
        value["effective_path"] = value["executable_path"] or value["detected_path"]
        value["available_models"] = [{"id": key, "label": label} for key, label in MODELS.items()]
        return value

    def status(self) -> dict:
        """Runs Claude Code's own version and sign-in checks. Sends nothing to a model and uses no quota."""
        value = self.get()
        state = {"installed": False, "version": None, "logged_in": False, "auth_method": "none", "subscription_type": None, "error": None}
        try:
            client = self.client()
            state["version"] = client.version()
            state["installed"] = True
            state.update(client.auth())
            if not state["logged_in"]:
                state["error"] = "Claude Code trên máy này chưa đăng nhập. Mở Claude Code và đăng nhập bằng tài khoản của chính bạn ở đó, rồi kiểm tra lại."
        except ClaudeCodeError as exc:
            state["error"] = str(exc)
        return {**value, "status": state}

    def save(self, expected_revision: int, executable_path: str, model: str) -> dict:
        path = executable_path.strip().strip('"')
        if model not in MODELS or len(path) > 1000:
            raise DomainError("Model hoặc đường dẫn Claude Code không hợp lệ.")
        if path:
            path = str(Path(path).expanduser())
            self.program(path).version()
        with self.db.transaction() as conn:
            current = conn.execute("SELECT revision,executable_path,model FROM claude_code_settings WHERE id=1").fetchone()
            if current["revision"] != expected_revision:
                raise DomainError("Cấu hình Claude Code đã đổi ở phiên khác; tải lại trước khi lưu.", 409)
            # A different program or model has not answered a request yet.
            changed = (current["executable_path"], current["model"]) != (path, model)
            conn.execute("UPDATE claude_code_settings SET revision=?,executable_path=?,model=?,verified_at=CASE WHEN ? THEN NULL ELSE verified_at END,"
                         "verified_model=CASE WHEN ? THEN NULL ELSE verified_model END,updated_at=? WHERE id=1",
                         (current["revision"] + 1, path, model, changed, changed, now()))
        return self.status()

    def test(self) -> dict:
        """One small real request, so "configured" can become "has answered". It uses the signed-in account's quota."""
        answer = self.client().ask(instructions="Reply through the structured output with ready set to true.", payload={"check": "studio"},
                                   schema=TEST_SCHEMA, model=self._row()["model"], timeout=180)
        if answer["output"].get("ready") is not True:
            raise ClaudeCodeError("Claude Code trả lời nhưng không đúng cấu trúc yêu cầu.")
        self.mark_verified(answer["model"])
        return self.status()

    def mark_verified(self, model: str | None) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE claude_code_settings SET verified_at=?,verified_model=? WHERE id=1", (now(), model))
