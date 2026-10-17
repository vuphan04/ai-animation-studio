"""The local store behind the app: projects, episodes, the audit trail and this PC's Unreal settings."""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import logs
from .db import Database
from .domain import DomainError, canonical

# Queues that drive the Unreal Editor; the project it opens cannot be swapped while one of them has work.
UNREAL_QUEUES = (("episode_ue_jobs", ""), ("ue_proxy_jobs", ""), ("final_jobs", " AND kind='UE_RENDER'"), ("ue_asset_scans", ""), ("style_capture_jobs", ""))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid() -> str:
    return uuid.uuid4().hex


def require(row, label: str = "resource") -> dict:
    if row is None:
        raise DomainError(f"{label} not found", 404)
    return dict(row)


# Audit outcomes that mean a job stopped without doing its work.
TROUBLE = {"blocked", "reconciling", "failed", "partial", "error", "interrupted", "expired"}


def redacted(value):
    if isinstance(value, dict):
        return {k: "[REDACTED]" if any(word in k.lower() for word in ("secret", "token", "password", "api_key")) else redacted(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redacted(v) for v in value]
    return value


class Studio:
    def __init__(self, data_root: Path):
        self.db = Database(data_root)
        with self.db.transaction() as conn:
            # A GPU lease that survived a restart belongs to a process started before it. It keeps the GPU
            # reserved until the step that owns it is told by the operator that the old process is gone.
            conn.execute("UPDATE gpu_heavy_leases SET state='RECONCILING' WHERE state='ACTIVE'")

    def _unreal_adapter(self):
        from .unreal_adapter import UnrealAdapter
        editor, project, _ = self._unreal_paths()
        if not editor or not project:
            raise DomainError("Chưa cấu hình Unreal Editor và project. Mở mục Cấu hình Unreal trong app.", 409)
        return UnrealAdapter(Path(editor), Path(project), self.db.data_root)

    def _unreal_paths(self) -> tuple[str, str, str]:
        with self.db.connect() as conn:
            row = conn.execute("SELECT editor_path,project_path FROM unreal_settings WHERE id=1").fetchone()
        if row:
            return row["editor_path"], row["project_path"], "APP"
        return os.environ.get("STUDIO_UNREAL_EDITOR", ""), os.environ.get("STUDIO_UNREAL_PROJECT", ""), "ENVIRONMENT"

    def unreal_settings(self) -> dict:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM unreal_settings WHERE id=1").fetchone()
        editor, project, source = self._unreal_paths()
        result = {"revision": row["revision"] if row else 0, "editor_path": editor,
                  "project_path": project, "source": source, "configured": False, "error": None}
        if editor and project:
            try:
                self._unreal_adapter()
                result["configured"] = True
            except (DomainError, ValueError, OSError) as exc:
                result["error"] = str(exc)
        return result

    def validate_unreal_settings(self, editor_path: str, project_path: str) -> dict:
        from .unreal_adapter import UnrealAdapter
        if not editor_path.strip() or not project_path.strip():
            raise DomainError("Chọn cả Unreal Editor và tệp .uproject.")
        try:
            adapter = UnrealAdapter(Path(editor_path.strip()), Path(project_path.strip()), self.db.data_root)
        except DomainError:
            raise
        except (OSError, ValueError) as exc:
            raise DomainError("Đường dẫn Unreal Editor hoặc .uproject không hợp lệ.") from exc
        return {"editor_path": str(adapter.editor), "project_path": str(adapter.project),
                "configured": True, "message": "Đường dẫn và plugin hợp lệ; chưa kiểm chứng asset hoặc lắp dựng trong Unreal."}

    def save_unreal_settings(self, editor_path: str, project_path: str, expected_revision: int) -> dict:
        valid = self.validate_unreal_settings(editor_path, project_path)
        with self.db.transaction() as conn:
            row = conn.execute("SELECT revision,editor_path,project_path FROM unreal_settings WHERE id=1").fetchone()
            revision = row["revision"] if row else 0
            if revision != expected_revision:
                raise DomainError("Cấu hình đã thay đổi ở phiên khác. Tải lại rồi lưu lại.", 409)
            if row and row["editor_path"] == valid["editor_path"] and row["project_path"] == valid["project_path"]:
                return self.unreal_settings()
            if any(conn.execute(f"SELECT 1 FROM {table} WHERE state IN ('QUEUED','RUNNING','RECONCILING'){extra} LIMIT 1").fetchone() for table, extra in UNREAL_QUEUES):
                raise DomainError("Còn công việc Unreal đang chờ/chạy/đối soát; hoàn tất chúng trước khi đổi project.", 409)
            if row:
                conn.execute("UPDATE unreal_settings SET revision=?,editor_path=?,project_path=?,updated_at=? WHERE id=1",
                             (revision + 1, valid["editor_path"], valid["project_path"], now()))
            else:
                conn.execute("INSERT INTO unreal_settings(id,revision,editor_path,project_path,updated_at) VALUES(1,1,?,?,?)",
                             (valid["editor_path"], valid["project_path"], now()))
            self.audit(conn, None, "settings.unreal.changed", "unreal", {"revision": revision + 1, "source": "APP"})
        return self.unreal_settings()

    def audit(self, conn, project_id: str | None, event: str, entity: str, detail: dict) -> None:
        text = canonical(redacted(detail))
        conn.execute("INSERT INTO audit(project_id,event_type,entity_id,detail_json,created_at) VALUES(?,?,?,?,?)", (project_id, event, entity, text, now()))
        # Every recorded action also lands in the log, under the area that recorded it (series, breakdown, budget…).
        outcome = event.rsplit(".", 1)[-1].rsplit("_", 1)[-1]
        logs.get(event.split(".", 1)[0]).log(logging.WARNING if outcome in TROUBLE else logging.INFO, "%s %s %s", event, entity, text[:400])

    def project(self, project_id: str) -> dict:
        with self.db.connect() as conn:
            return require(conn.execute("SELECT id,name,root,created_at FROM projects WHERE id=?", (project_id,)).fetchone(), "project")

    def projects(self) -> list[dict]:
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT id,name,root,created_at FROM projects ORDER BY created_at")]

    def create_project(self, name: str) -> dict:
        if not name.strip():
            raise DomainError("name is required")
        project_id = uid()
        root = (self.db.data_root / "projects" / project_id).resolve()
        root.mkdir(parents=True)
        # Each step writes below its own folder; these are the ones an operator is likely to open by hand.
        for folder in ("media", "audio", "delivery"):
            (root / folder).mkdir(parents=True)
        item = {"id": project_id, "name": name.strip(), "root": str(root), "created_at": now()}
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO projects(id,name,root,created_at,workflow_version) VALUES(:id,:name,:root,:created_at,'V4_5')", item)
            self.audit(conn, project_id, "project.created", project_id, {"name": item["name"]})
        return item

    def create_episode(self, project_id: str, name: str, ordinal: int) -> dict:
        self.project(project_id)
        if not name.strip() or ordinal < 0:
            raise DomainError("episode name and nonnegative ordinal required")
        item = {"id": uid(), "project_id": project_id, "name": name.strip(), "ordinal": ordinal, "code": f"EP{ordinal:02d}"}
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO episodes(id,project_id,name,ordinal,code) VALUES(:id,:project_id,:name,:ordinal,:code)", item)
            self.audit(conn, project_id, "episode.created", item["id"], item)
        return item

    def episodes(self, project_id: str) -> list[dict]:
        self.project(project_id)
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM episodes WHERE project_id=? ORDER BY ordinal", (project_id,))]

    def last_event_id(self, project_id: str | None = None) -> int:
        with self.db.connect() as conn:
            row = conn.execute("SELECT COALESCE(MAX(id),0) FROM audit WHERE project_id=?", (project_id,)).fetchone() if project_id \
                else conn.execute("SELECT COALESCE(MAX(id),0) FROM audit").fetchone()
        return row[0]

    def events(self, after: int = 0, project_id: str | None = None) -> list[dict]:
        with self.db.connect() as conn:
            if project_id:
                self.project(project_id)
                rows = conn.execute("SELECT * FROM audit WHERE id>? AND project_id=? ORDER BY id LIMIT 100", (after, project_id))
            else:
                rows = conn.execute("SELECT * FROM audit WHERE id>? ORDER BY id LIMIT 100", (after,))
            return [{**dict(r), "detail": json.loads(r["detail_json"])} for r in rows]
