"""Local HunyuanVideo-Foley for workflow V4.5 step 12, run as the command-line tool the operator installed.

Saved settings mean the four paths exist on this machine. They do not show that the model loads or fits
this GPU; only a finished Foley job does. The command follows the tool's published `infer.py` usage and
has not been run from this app against a real installation.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .domain import DomainError
from .service import now


FIELDS = ("python_path", "script_path", "model_path", "config_path")
TIMEOUT_SECONDS = 2 * 3600


def check(paths: dict) -> dict:
    clean = {}
    for field in FIELDS:
        value = paths.get(field)
        if not isinstance(value, str) or not value.strip():
            raise DomainError("Chọn đủ bốn mục: python.exe, infer.py, thư mục model và file cấu hình.")
        clean[field] = str(Path(value.strip()))
    python, script, model, config = (Path(clean[field]) for field in FIELDS)
    if not python.is_file() or not python.name.lower().startswith("python") or python.suffix.lower() != ".exe":
        raise DomainError("Chọn python.exe của môi trường đã cài HunyuanVideo-Foley.")
    if not script.is_file() or script.suffix.lower() != ".py":
        raise DomainError("Chọn file infer.py trong thư mục HunyuanVideo-Foley.")
    if not model.is_dir() or not any(model.iterdir()):
        raise DomainError("Chọn thư mục chứa model HunyuanVideo-Foley đã tải về (thư mục không được trống).")
    if not config.is_file() or config.suffix.lower() not in (".yaml", ".yml"):
        raise DomainError("Chọn file cấu hình .yaml của model.")
    return clean


class FoleyToolSettings:
    def __init__(self, studio):
        self.studio = studio
        self.db = studio.db

    def get(self) -> dict:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM foley_tool_settings WHERE id=1").fetchone()
        result = {"revision": row["revision"] if row else 0, **{field: row[field] if row else "" for field in FIELDS}, "configured": False, "error": None,
                  "note": "Đã lưu nghĩa là bốn đường dẫn có trên máy; chưa chứng minh model chạy được trên GPU này."}
        if row:
            try:
                check(result)
                result["configured"] = True
            except DomainError as exc:
                result["error"] = str(exc)
        return result

    def save(self, expected_revision: int, paths: dict) -> dict:
        clean = check(paths)
        with self.db.transaction() as conn:
            row = conn.execute("SELECT revision FROM foley_tool_settings WHERE id=1").fetchone()
            if (row["revision"] if row else 0) != expected_revision:
                raise DomainError("Cấu hình Foley đã đổi ở phiên khác; tải lại trước khi lưu.", 409)
            conn.execute("INSERT OR REPLACE INTO foley_tool_settings(id,revision,python_path,script_path,model_path,config_path,updated_at) VALUES(1,?,?,?,?,?,?)",
                         (expected_revision + 1, *(clean[field] for field in FIELDS), now()))
            self.studio.audit(conn, None, "settings.foley_tool.changed", "hunyuan-foley", {"revision": expected_revision + 1})
        return self.get()


class HunyuanFoleyRunner:
    kind = "HUNYUAN_FOLEY_LOCAL"

    def __init__(self, settings: FoleyToolSettings):
        self.settings = settings

    def generate(self, video: Path, prompt: str, output_dir: Path) -> Path:
        """Run the model on one silent clip. Returns the audio it wrote, or the video carrying it."""
        config = self.settings.get()
        if not config["configured"]:
            raise DomainError(config["error"] or "Cấu hình HunyuanVideo-Foley ở Cài đặt → Foley local trước khi tạo Foley.", 409)
        output_dir.mkdir(parents=True, exist_ok=True)
        command = [config["python_path"], config["script_path"], "--model_path", config["model_path"], "--config_path", config["config_path"],
                   "--single_video", str(video), "--single_prompt", prompt, "--output_dir", str(output_dir)]
        try:
            done = subprocess.run(command, cwd=str(Path(config["script_path"]).parent), capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=TIMEOUT_SECONDS, check=False)
        except subprocess.TimeoutExpired as exc:
            raise DomainError("HunyuanVideo-Foley chạy quá 2 giờ cho một shot; đã dừng.", 409) from exc
        except OSError as exc:
            raise DomainError(f"Không chạy được HunyuanVideo-Foley: {type(exc).__name__}.", 409) from exc
        if done.returncode != 0:
            lines = [line for line in (done.stderr or done.stdout or "").strip().splitlines() if line.strip()]
            raise DomainError("HunyuanVideo-Foley báo lỗi: " + (lines[-1][:300] if lines else f"mã thoát {done.returncode}"), 409)
        for pattern in ("*.wav", "*.flac", "*.mp4"):
            found = sorted(output_dir.rglob(pattern), key=lambda path: path.stat().st_mtime)
            if found:
                return found[-1]
        raise DomainError("HunyuanVideo-Foley chạy xong nhưng không ghi file âm thanh nào vào thư mục kết quả.", 409)
