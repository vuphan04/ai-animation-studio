from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import gpu_queue
from .adapters import FakeAdapter
from .db import Database, row_dict
from .domain import DEPENDENT, SUBSYSTEMS, DomainError, canonical, digest, safe_relative, validate_manifest


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid() -> str:
    return uuid.uuid4().hex


def require(row, label: str = "resource") -> dict:
    if row is None:
        raise DomainError(f"{label} not found", 404)
    return dict(row)


def redacted(value):
    if isinstance(value, dict):
        return {k: "[REDACTED]" if any(word in k.lower() for word in ("secret", "token", "password", "api_key")) else redacted(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redacted(v) for v in value]
    return value


def contains_secret_key(value) -> bool:
    if isinstance(value, dict):
        return any(any(word in key.lower() for word in ("secret", "token", "password", "api_key")) or contains_secret_key(child) for key, child in value.items())
    if isinstance(value, list):
        return any(contains_secret_key(child) for child in value)
    return False


class Studio:
    def __init__(self, data_root: Path, *, recover_running: bool = True):
        self.db = Database(data_root)
        self.adapter = FakeAdapter()
        if recover_running:
            with self.db.transaction() as conn:
                conn.execute("UPDATE jobs SET state='RECONCILING',error='backend restarted during attempt',updated_at=? WHERE state IN ('RUNNING','CANCEL_REQUESTED')", (now(),))
                conn.execute("DELETE FROM resource_leases WHERE job_id IN (SELECT id FROM jobs WHERE state='RECONCILING')")
                # Local control-flow jobs run in this process, so their GPU lease cannot outlive it.
                conn.execute("DELETE FROM gpu_heavy_leases WHERE owner_kind='JOB'")
                conn.execute("UPDATE unreal_episode_jobs SET state='RECONCILING',error='backend restarted during Unreal attempt',updated_at=? WHERE state='RUNNING'", (now(),))
                conn.execute("UPDATE unreal_episode_attempts SET state='RECONCILING',ended_at=? WHERE state='RUNNING'", (now(),))
                # The old UE process may outlive the backend. Hold its lease until observe/reconcile.
                conn.execute("UPDATE voice_jobs SET state='RECONCILING',error='backend restarted during voice attempt',updated_at=? WHERE state='RUNNING'", (now(),))
                conn.execute("UPDATE voice_job_attempts SET state='RECONCILING',ended_at=? WHERE state='RUNNING'", (now(),))
                conn.execute("DELETE FROM voice_resource_lease")

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
            active = conn.execute("SELECT 1 FROM unreal_episode_jobs WHERE state IN ('QUEUED','RUNNING','RECONCILING') LIMIT 1").fetchone()
            if active:
                raise DomainError("Còn công việc Unreal đang chờ/chạy/đối soát; hoàn tất chúng trước khi đổi project.", 409)
            if row:
                conn.execute("UPDATE unreal_settings SET revision=?,editor_path=?,project_path=?,updated_at=? WHERE id=1",
                             (revision + 1, valid["editor_path"], valid["project_path"], now()))
            else:
                conn.execute("INSERT INTO unreal_settings(id,revision,editor_path,project_path,updated_at) VALUES(1,1,?,?,?)",
                             (valid["editor_path"], valid["project_path"], now()))
            self.audit(conn, None, "settings.unreal.changed", "unreal", {"revision": revision + 1, "source": "APP"})
        return self.unreal_settings()

    def preview_unreal_episode(self, project_id: str, episode_id: str, shot_codes: list[str] | None = None) -> dict:
        from .p2 import compile_episode
        _, project_path, _ = self._unreal_paths()
        target_project = str(Path(project_path).expanduser().resolve()) if project_path else None
        return compile_episode(self, project_id, episode_id, shot_codes=shot_codes, target_project=target_project)

    def preflight_unreal_episode(self, project_id: str, episode_id: str, shot_codes: list[str] | None = None) -> dict:
        operation = self.preview_unreal_episode(project_id, episode_id, shot_codes)
        return self._unreal_adapter().execute(operation, "preflight")

    def enqueue_unreal_episode(self, project_id: str, episode_id: str, shot_codes: list[str] | None = None) -> dict:
        operation = self.preview_unreal_episode(project_id, episode_id, shot_codes)
        self._unreal_adapter()  # Fail before enqueue if no real UE installation is configured.
        with self.db.transaction() as conn:
            existing = conn.execute("SELECT * FROM unreal_episode_jobs WHERE operation_id=?", (operation["operation_id"],)).fetchone()
            if existing:
                return dict(existing)
            timestamp = now()
            item = {"id": uid(), "project_id": project_id, "episode_id": episode_id,
                    "operation_id": operation["operation_id"], "input_hash": operation["input_hash"],
                    "operation_json": canonical(operation), "state": "QUEUED", "attempt": 0,
                    "result_json": None, "error": None, "created_at": timestamp, "updated_at": timestamp}
            conn.execute("INSERT INTO unreal_episode_jobs(id,project_id,episode_id,operation_id,input_hash,operation_json,state,attempt,result_json,error,created_at,updated_at) VALUES(:id,:project_id,:episode_id,:operation_id,:input_hash,:operation_json,:state,:attempt,:result_json,:error,:created_at,:updated_at)", item)
            self.audit(conn, project_id, "unreal.queued", item["id"], {"operation_id": operation["operation_id"]})
            return item

    def unreal_jobs(self, project_id: str) -> list[dict]:
        self.project(project_id)
        with self.db.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT id,project_id,episode_id,operation_id,input_hash,state,attempt,error,created_at,updated_at FROM unreal_episode_jobs WHERE project_id=? ORDER BY created_at DESC", (project_id,))]

    def unreal_job(self, job_id: str) -> dict:
        with self.db.connect() as conn:
            row = require(conn.execute("SELECT * FROM unreal_episode_jobs WHERE id=?", (job_id,)).fetchone(), "Unreal job")
            row.pop("operation_json")
            row["attempts"] = [dict(a) for a in conn.execute("SELECT * FROM unreal_episode_attempts WHERE job_id=? ORDER BY attempt", (job_id,))]
            if row["result_json"]:
                row["result"] = json.loads(row.pop("result_json"))
            else:
                row.pop("result_json")
            return row

    def _record_unreal_result(self, conn, job: dict, operation: dict, result: dict) -> None:
        receipt = conn.execute("SELECT id,output_json FROM receipts WHERE operation_id=?", (job["operation_id"],)).fetchone()
        if receipt:
            if json.loads(receipt["output_json"])["output_fingerprint"] != result["output_fingerprint"]:
                raise DomainError("same Unreal operation has conflicting output fingerprint", 409)
        else:
            conn.execute("INSERT INTO receipts(id,operation_id,input_hash,status,output_json,created_at) VALUES(?,?,?,'COMPLETED',?,?)",
                         (uid(), job["operation_id"], job["input_hash"], canonical(result), now()))
        for shot in operation["shots"]:
            linked = conn.execute("SELECT 1 FROM unreal_job_artifacts WHERE job_id=? AND artifact_id IN (SELECT id FROM artifact_versions WHERE shot_id=? AND role='body')", (job["id"], shot["shot_id"])).fetchone()
            if linked:
                continue
            current = conn.execute("SELECT current_revision FROM shots WHERE id=?", (shot["shot_id"],)).fetchone()
            validity = "CURRENT" if current and current[0] == shot["shot_revision"] else "STALE"
            reusable = conn.execute("SELECT id FROM artifact_versions WHERE shot_id=? AND role='body' AND input_hash=? AND provenance='UNREAL_EDITOR_PYTHON' AND validity='CURRENT' ORDER BY version DESC LIMIT 1", (shot["shot_id"], shot["input_hash"])).fetchone()
            if reusable and validity == "CURRENT":
                conn.execute("INSERT INTO unreal_job_artifacts(job_id,artifact_id) VALUES(?,?)", (job["id"], reusable["id"]))
                continue
            version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM artifact_versions WHERE shot_id=? AND role='body'", (shot["shot_id"],)).fetchone()[0]
            artifact_id = uid()
            conn.execute("INSERT INTO artifact_versions(id,project_id,shot_id,role,version,input_hash,relative_path,provenance,validity,created_at) VALUES(?,?,?,?,?,?,NULL,?,?,?)",
                         (artifact_id, job["project_id"], shot["shot_id"], "body", version, shot["input_hash"], "UNREAL_EDITOR_PYTHON", validity, now()))
            conn.execute("INSERT INTO unreal_job_artifacts(job_id,artifact_id) VALUES(?,?)", (job["id"], artifact_id))
            if validity == "CURRENT":
                conn.execute("UPDATE subsystem_states SET synced_fingerprint=?,observed_fingerprint=?,validity='CURRENT',review='NEEDS_REVIEW',state_revision=state_revision+1 WHERE shot_id=? AND subsystem='body'",
                             (result["output_fingerprint"], result["output_fingerprint"], shot["shot_id"]))

    def process_next_unreal_episode(self) -> dict | None:
        from .p2 import validate_result
        with self.db.transaction() as conn:
            if conn.execute("SELECT 1 FROM unreal_resource_leases WHERE resource='UNREAL_EDITOR'").fetchone() or gpu_queue.holder(conn):
                return None
            row = conn.execute("SELECT * FROM unreal_episode_jobs WHERE state='QUEUED' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            job = dict(row)
            attempt_id = uid()
            gpu_queue.try_acquire(conn, job["operation_id"], "UNREAL")
            conn.execute("INSERT INTO unreal_resource_leases(resource,job_id,acquired_at) VALUES('UNREAL_EDITOR',?,?)", (job["id"], now()))
            conn.execute("INSERT INTO unreal_episode_attempts(id,job_id,attempt,state,started_at) VALUES(?,?,?,'RUNNING',?)", (attempt_id, job["id"], job["attempt"] + 1, now()))
            conn.execute("UPDATE unreal_episode_jobs SET state='RUNNING',attempt=attempt+1,updated_at=? WHERE id=?", (now(), job["id"]))
            self.audit(conn, job["project_id"], "unreal.running", job["id"], {"attempt": job["attempt"] + 1})
        started_apply = False
        try:
            operation = json.loads(job["operation_json"])
            current = self.preview_unreal_episode(job["project_id"], job["episode_id"], [s["shot_code"] for s in operation["shots"]])
            if current["operation_id"] != job["operation_id"]:
                raise DomainError("episode input changed since enqueue", 409)
            adapter = self._unreal_adapter()
            started_apply = True
            result = adapter.execute(operation, "apply")
            validate_result(operation, result)
            with self.db.transaction() as conn:
                self._record_unreal_result(conn, job, operation, result)
                conn.execute("UPDATE unreal_episode_jobs SET state='COMPLETED',result_json=?,error=NULL,updated_at=? WHERE id=?", (canonical(result), now(), job["id"]))
                conn.execute("UPDATE unreal_episode_attempts SET state='COMPLETED',ended_at=? WHERE id=?", (now(), attempt_id))
                conn.execute("DELETE FROM unreal_resource_leases WHERE resource='UNREAL_EDITOR' AND job_id=?", (job["id"],))
                gpu_queue.release(conn, job["operation_id"])
                self.audit(conn, job["project_id"], "unreal.completed", job["id"], {"fingerprint": result["output_fingerprint"]})
        except Exception as exc:
            state = "RECONCILING" if started_apply else "BLOCKED"
            with self.db.transaction() as conn:
                conn.execute("UPDATE unreal_episode_jobs SET state=?,error=?,updated_at=? WHERE id=?", (state, f"{type(exc).__name__}; {'inspect Unreal output before retry' if started_apply else 'prewrite gate failed'}", now(), job["id"]))
                conn.execute("UPDATE unreal_episode_attempts SET state=?,ended_at=? WHERE id=?", (state, now(), attempt_id))
                if not started_apply:
                    conn.execute("DELETE FROM unreal_resource_leases WHERE resource='UNREAL_EDITOR' AND job_id=?", (job["id"],))
                    gpu_queue.release(conn, job["operation_id"])
                self.audit(conn, job["project_id"], "unreal." + state.lower(), job["id"], {"reason": type(exc).__name__})
        return self.unreal_job(job["id"])

    def reconcile_unreal_episode(self, job_id: str, worker_stopped: bool = False) -> dict:
        from .p2 import validate_result
        with self.db.connect() as conn:
            job = require(conn.execute("SELECT * FROM unreal_episode_jobs WHERE id=?", (job_id,)).fetchone(), "Unreal job")
        if job["state"] != "RECONCILING":
            raise DomainError("Unreal job is not reconciling", 409)
        if not worker_stopped:
            raise DomainError('Dừng Unreal worker cũ rồi xác nhận trong giao diện trước khi observe/reclaim lease.', 409)
        operation = json.loads(job["operation_json"])
        result = self._unreal_adapter().execute(operation, "observe")
        validate_result(operation, result)
        with self.db.transaction() as conn:
            self._record_unreal_result(conn, dict(job), operation, result)
            conn.execute("UPDATE unreal_episode_jobs SET state='COMPLETED',result_json=?,error=NULL,updated_at=? WHERE id=? AND state='RECONCILING'", (canonical(result), now(), job_id))
            conn.execute("UPDATE unreal_episode_attempts SET state='COMPLETED',ended_at=? WHERE job_id=? AND attempt=?", (now(), job_id, job["attempt"]))
            conn.execute("DELETE FROM unreal_resource_leases WHERE job_id=?", (job_id,))
            gpu_queue.release(conn, job["operation_id"])
            self.audit(conn, job["project_id"], "unreal.reconciled", job_id, {"fingerprint": result["output_fingerprint"]})
        return self.unreal_job(job_id)

    def retry_unreal_episode(self, job_id: str) -> dict:
        with self.db.transaction() as conn:
            job = require(conn.execute("SELECT * FROM unreal_episode_jobs WHERE id=?", (job_id,)).fetchone(), "Unreal job")
            if job["state"] != "BLOCKED" or job["result_json"]:
                raise DomainError("Unreal retry requires a known prewrite block with no result", 409)
            operation = json.loads(job["operation_json"])
            current = self.preview_unreal_episode(job["project_id"], job["episode_id"], [s["shot_code"] for s in operation["shots"]])
            if current["operation_id"] != job["operation_id"]:
                raise DomainError("episode input changed; create a new operation", 409)
            self._unreal_adapter()
            conn.execute("UPDATE unreal_episode_jobs SET state='QUEUED',operation_json=?,error=NULL,updated_at=? WHERE id=?", (canonical(current), now(), job_id))
            self.audit(conn, job["project_id"], "unreal.retry_requested", job_id, {"operation_id": job["operation_id"]})
        return self.unreal_job(job_id)

    def audit(self, conn, project_id: str | None, event: str, entity: str, detail: dict) -> None:
        conn.execute("INSERT INTO audit(project_id,event_type,entity_id,detail_json,created_at) VALUES(?,?,?,?,?)", (project_id, event, entity, canonical(redacted(detail)), now()))

    def project(self, project_id: str) -> dict:
        with self.db.connect() as conn:
            return require(conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone(), "project")

    def projects(self) -> list[dict]:
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM projects ORDER BY created_at")]

    def create_project(self, name: str) -> dict:
        if not name.strip():
            raise DomainError("name is required")
        project_id = uid()
        root = (self.db.data_root / "projects" / project_id).resolve()
        root.mkdir(parents=True)
        for folder in ("manifests", "registry", "media", "caches", "renders", "audio/dialogue", "audio/foley", "audio/sfx", "audio/ambience", "audio/music", "delivery", "logs"):
            (root / folder).mkdir(parents=True)
        item = {"id": project_id, "name": name.strip(), "root": str(root), "created_at": now(), "workflow_version": "V4_5"}
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO projects(id,name,root,created_at,workflow_version) VALUES(:id,:name,:root,:created_at,:workflow_version)", item)
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

    def registry(self, project_id: str) -> list[dict]:
        self.project(project_id)
        with self.db.connect() as conn:
            rows = conn.execute("SELECT a.* FROM asset_versions a JOIN (SELECT registry_id,MAX(version) version FROM asset_versions WHERE project_id=? GROUP BY registry_id) latest ON a.registry_id=latest.registry_id AND a.version=latest.version WHERE a.project_id=? ORDER BY a.registry_id", (project_id, project_id))
            return [self._asset(r) for r in rows]

    def search_motion(self, project_id: str, skeleton_id: str, tags: list[str], paired: bool = False) -> dict:
        from .p2 import choose_motion
        if not skeleton_id or not isinstance(tags, list) or not tags or any(not isinstance(tag, str) or not tag for tag in tags):
            raise DomainError("motion query requires skeleton ID and semantic tags")
        return choose_motion(self.registry(project_id), skeleton_id=skeleton_id, tags=tags, paired=paired)

    def _asset(self, row) -> dict:
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json"))
        return item

    def add_asset(self, project_id: str, registry_id: str, kind: str, metadata: dict, relative_path: str | None = None, checksum: str | None = None) -> dict:
        project = self.project(project_id)
        if not registry_id or not registry_id.replace("_", "").isalnum() or kind not in ("character", "environment", "style_reference", "anchor", "animation", "paired_animation", "dialogue", "voice_profile", "retarget_profile", "render_profile", "groom", "costume", "niagara_effect"):
            raise DomainError("invalid registry ID or kind")
        if not isinstance(metadata, dict) or contains_secret_key(metadata):
            raise DomainError("metadata contains forbidden secret field")
        rel = safe_relative(Path(project["root"]), relative_path) if relative_path is not None else None
        with self.db.transaction() as conn:
            version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM asset_versions WHERE project_id=? AND registry_id=?", (project_id, registry_id)).fetchone()[0]
            item = {"id": uid(), "project_id": project_id, "registry_id": registry_id, "kind": kind, "version": version, "relative_path": rel, "metadata_json": canonical(metadata), "checksum": checksum, "created_at": now()}
            conn.execute("INSERT INTO asset_versions(id,project_id,registry_id,kind,version,relative_path,metadata_json,checksum,created_at) VALUES(:id,:project_id,:registry_id,:kind,:version,:relative_path,:metadata_json,:checksum,:created_at)", item)
            self.audit(conn, project_id, "registry.asset_version", item["id"], {"registry_id": registry_id, "version": version})
            if version > 1:
                if kind in ("groom", "costume", "niagara_effect"):
                    cache_kind = {"groom": "hair", "costume": "cloth", "niagara_effect": "vfx"}[kind]
                    previous = conn.execute("SELECT id FROM asset_versions WHERE project_id=? AND registry_id=? AND version=?", (project_id, registry_id, version - 1)).fetchone()
                    if previous:
                        cache_shots = conn.execute("SELECT DISTINCT shot_id FROM simulation_cache_versions WHERE project_id=? AND subsystem=? AND json_extract(source_json,'$.asset_version_id')=?", (project_id, cache_kind, previous["id"])).fetchall()
                        conn.execute("UPDATE simulation_cache_versions SET validity='STALE' WHERE project_id=? AND subsystem=? AND json_extract(source_json,'$.asset_version_id')=?", (project_id, cache_kind, previous["id"]))
                        for cache_shot in cache_shots:
                            conn.execute("UPDATE subsystem_states SET validity='STALE',review='NEEDS_REVIEW',state_revision=state_revision+1 WHERE shot_id=? AND subsystem IN (?, 'render')", (cache_shot["shot_id"], cache_kind))
                            conn.execute("UPDATE artifact_versions SET validity='STALE' WHERE shot_id=? AND role='render'", (cache_shot["shot_id"],))
                rows = conn.execute("SELECT s.id,r.resolved_registry_json FROM shots s JOIN shot_revisions r ON r.shot_id=s.id AND r.revision=s.current_revision WHERE s.project_id=?", (project_id,))
                for row in rows:
                    if registry_id in json.loads(row["resolved_registry_json"]):
                        affected = ("face", "audio", "render", "sound") if kind in ("dialogue", "voice_profile") else SUBSYSTEMS
                        for subsystem in affected:
                            conn.execute("UPDATE subsystem_states SET validity='STALE',review='NEEDS_REVIEW',state_revision=state_revision+1 WHERE shot_id=? AND subsystem=?", (row["id"], subsystem))
                            conn.execute("UPDATE artifact_versions SET validity='STALE' WHERE shot_id=? AND role=?", (row["id"], subsystem))
                            if subsystem in ("hair", "cloth", "vfx"):
                                conn.execute("UPDATE simulation_cache_versions SET validity='STALE' WHERE shot_id=? AND subsystem=?", (row["id"], subsystem))
                        if kind in ("dialogue", "voice_profile"):
                            old_id = json.loads(row["resolved_registry_json"])[registry_id]["asset_version_id"]
                            conn.execute("UPDATE voice_takes SET validity='STALE' WHERE shot_id=? AND (dialogue_version_id=? OR voice_profile_version_id=?)", (row["id"], old_id, old_id))
                            conn.execute("UPDATE timing_revisions SET validity='STALE',review='NEEDS_REVIEW' WHERE shot_id=?", (row["id"],))
                        conn.execute("UPDATE video_reference_packages SET validity='STALE' WHERE shot_id=?", (row["id"],))
                        conn.execute("UPDATE video_takes SET validity='STALE' WHERE shot_id=?", (row["id"],))
                        conn.execute("UPDATE picture_locks SET validity='STALE' WHERE shot_id=?", (row["id"],))
                        conn.execute("UPDATE sound_stems SET validity='STALE' WHERE shot_id=?", (row["id"],))
                        conn.execute("UPDATE delivery_packages SET state='STALE' WHERE episode_id=(SELECT episode_id FROM shots WHERE id=?) AND state!='STALE'", (row["id"],))
                        self.audit(conn, project_id, "shot.registry_stale", row["id"], {"registry_id": registry_id, "new_asset_version": version})
                if kind == "style_reference":
                    prior_style = conn.execute("SELECT id FROM asset_versions WHERE project_id=? AND registry_id=? AND version=?", (project_id, registry_id, version - 1)).fetchone()
                    for ref in conn.execute("SELECT shot_id,references_json FROM video_reference_packages WHERE project_id=? AND validity='CURRENT'", (project_id,)).fetchall():
                        if prior_style and json.loads(ref["references_json"])["assets"]["style"] == prior_style["id"]:
                            conn.execute("UPDATE video_reference_packages SET validity='STALE' WHERE shot_id=?", (ref["shot_id"],))
                            conn.execute("UPDATE video_takes SET validity='STALE' WHERE shot_id=? AND route IN ('SEEDANCE','HYBRID')", (ref["shot_id"],))
                            conn.execute("UPDATE picture_locks SET validity='STALE' WHERE shot_id=?", (ref["shot_id"],))
                            conn.execute("UPDATE sound_stems SET validity='STALE' WHERE shot_id=?", (ref["shot_id"],))
                            conn.execute("UPDATE delivery_packages SET state='STALE' WHERE episode_id=(SELECT episode_id FROM shots WHERE id=?) AND state!='STALE'", (ref["shot_id"],))
        return self._asset(item)

    def _registry_map(self, conn, project_id: str) -> dict:
        rows = conn.execute("SELECT a.* FROM asset_versions a JOIN (SELECT registry_id,MAX(version) version FROM asset_versions WHERE project_id=? GROUP BY registry_id) latest ON a.registry_id=latest.registry_id AND a.version=latest.version WHERE a.project_id=?", (project_id, project_id))
        return {r["registry_id"]: self._asset(r) for r in rows}

    def _pin_registry(self, manifest: dict, registry: dict) -> dict:
        ids = {manifest["environment_id"]}
        if manifest.get("render_profile_id"):
            ids.add(manifest["render_profile_id"])
        if manifest.get("camera_intent", {}).get("subject"):
            ids.add(manifest["camera_intent"]["subject"])
        for performance in manifest["performances"]:
            ids.update(performance[key] for key in ("character_id", "anchor_id", "animation_id"))
            for key in ("retarget_profile_id", "paired_asset_id"):
                if performance.get(key):
                    ids.add(performance[key])
            if performance.get("dialogue_id"):
                ids.add(performance["dialogue_id"])
            if performance.get("voice_profile_id"):
                ids.add(performance["voice_profile_id"])
        return {asset_id: {"asset_version_id": registry[asset_id]["id"], "version": registry[asset_id]["version"], "checksum": registry[asset_id]["checksum"], "metadata_hash": digest(registry[asset_id]["metadata"])} for asset_id in sorted(ids)}

    def _link_revision_assets(self, conn, revision_id: str, resolved: dict) -> None:
        for registry_id, pinned in resolved.items():
            conn.execute("INSERT INTO revision_assets(shot_revision_id,asset_version_id,registry_id) VALUES(?,?,?)", (revision_id, pinned["asset_version_id"], registry_id))

    def _siblings(self, conn, episode_id: str, exclude_shot_id: str | None = None) -> list[dict]:
        rows = conn.execute("SELECT s.id,r.manifest_json FROM shots s JOIN shot_revisions r ON r.shot_id=s.id AND r.revision=s.current_revision WHERE s.episode_id=?", (episode_id,))
        return [json.loads(r["manifest_json"]) for r in rows if r["id"] != exclude_shot_id]

    def shots(self, project_id: str) -> list[dict]:
        self.project(project_id)
        with self.db.connect() as conn:
            return [self._shot(conn, dict(r)) for r in conn.execute("SELECT * FROM shots WHERE project_id=? ORDER BY episode_id,ordinal", (project_id,))]

    def _shot(self, conn, shot: dict) -> dict:
        revision = conn.execute("SELECT * FROM shot_revisions WHERE shot_id=? AND revision=?", (shot["id"], shot["current_revision"])).fetchone()
        shot["manifest"] = json.loads(revision["manifest_json"])
        shot["resolved_registry"] = json.loads(revision["resolved_registry_json"])
        shot["input_hash"] = revision["input_hash"]
        shot["states"] = [dict(r) for r in conn.execute("SELECT * FROM subsystem_states WHERE shot_id=? ORDER BY subsystem", (shot["id"],))]
        shot["approval"] = row_dict(conn.execute("SELECT * FROM approvals WHERE project_id=? AND scope='plan' AND scope_id=? AND exact_version=?", (shot["project_id"], shot["id"], shot["current_revision"])).fetchone())
        if shot["approval"]:
            shot["approval"]["decision"] = json.loads(shot["approval"].pop("decision_json"))
        return shot

    def shot(self, shot_id: str) -> dict:
        with self.db.connect() as conn:
            return self._shot(conn, require(conn.execute("SELECT * FROM shots WHERE id=?", (shot_id,)).fetchone(), "shot"))

    def create_shot(self, project_id: str, episode_id: str, ordinal: int, manifest: dict,
                    expected_input_hash: str | None = None, after_write=None) -> dict:
        self.project(project_id)
        with self.db.transaction() as conn:
            episode = require(conn.execute("SELECT * FROM episodes WHERE id=? AND project_id=?", (episode_id, project_id)).fetchone(), "episode")
            registry = self._registry_map(conn, project_id)
            result = validate_manifest(manifest, registry, self._siblings(conn, episode["id"]))
            resolved = self._pin_registry(manifest, registry)
            if expected_input_hash is not None and digest({"manifest": manifest, "registry": resolved}) != expected_input_hash:
                raise DomainError("registry version changed before planning apply", 409)
            shot_id, timestamp = uid(), now()
            conn.execute("INSERT INTO shots(id,project_id,episode_id,code,ordinal,current_revision) VALUES(?,?,?,?,?,1)", (shot_id, project_id, episode_id, manifest["shot_id"], ordinal))
            revision_id = uid()
            conn.execute("INSERT INTO shot_revisions(id,shot_id,revision,manifest_json,resolved_registry_json,input_hash,created_at) VALUES(?,?,?,?,?,?,?)", (revision_id, shot_id, 1, canonical(manifest), canonical(resolved), digest({"manifest": manifest, "registry": resolved}), timestamp))
            self._link_revision_assets(conn, revision_id, resolved)
            for subsystem in SUBSYSTEMS:
                conn.execute("INSERT INTO subsystem_states(shot_id,subsystem,mode,lock_state,validity,review,state_revision) VALUES(?,?,'AUTO','UNLOCKED','CURRENT','NEEDS_REVIEW',1)", (shot_id, subsystem))
            self.audit(conn, project_id, "shot.created", shot_id, {"code": manifest["shot_id"], **result})
            if after_write is not None:
                after_write(conn, shot_id, 1)
            return self._shot(conn, require(conn.execute("SELECT * FROM shots WHERE id=?", (shot_id,)).fetchone()))

    def _changed(self, old: dict, new: dict) -> tuple[list[str], set[str]]:
        keys = sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))
        direct = set()
        for key in keys:
            if key == "performances":
                before, after = old.get("performances", []), new.get("performances", [])
                old_by_id = {p.get("performance_id"): p for p in before}
                new_by_id = {p.get("performance_id"): p for p in after}
                if len(old_by_id) == len(before) == len(new_by_id) == len(after) and old_by_id.keys() == new_by_id.keys() and None not in old_by_id:
                    changed = {field for pid in old_by_id for field in old_by_id[pid].keys() | new_by_id[pid].keys()
                               if old_by_id[pid].get(field) != new_by_id[pid].get(field)}
                    direct.update(("face", "audio") if changed <= {"dialogue_id", "voice_profile_id", "dialogue_start_local_frame"} else ("body", "face", "audio"))
                else:
                    direct.update(("body", "face", "audio"))
            else:
                direct.update({"timeline": SUBSYSTEMS, "fps": SUBSYSTEMS, "camera_intent": ("camera",), "environment_id": ("environment",), "route": ("render",)}.get(key, SUBSYSTEMS))
        affected = set(direct)
        pending = list(direct)
        while pending:
            for subsystem in DEPENDENT.get(pending.pop(), ()):
                if subsystem not in affected:
                    affected.add(subsystem)
                    pending.append(subsystem)
        return keys, affected

    def preview_revision(self, shot_id: str, expected_revision: int, manifest: dict) -> dict:
        with self.db.connect() as conn:
            shot = require(conn.execute("SELECT * FROM shots WHERE id=?", (shot_id,)).fetchone(), "shot")
            if shot["current_revision"] != expected_revision:
                raise DomainError("shot revision conflict", 409)
            if manifest.get("shot_id") != shot["code"]:
                raise DomainError("shot_id cannot change")
            registry = self._registry_map(conn, shot["project_id"])
            validation = validate_manifest(manifest, registry, self._siblings(conn, shot["episode_id"], shot_id))
            resolved = self._pin_registry(manifest, registry)
            old_revision = conn.execute("SELECT manifest_json,resolved_registry_json FROM shot_revisions WHERE shot_id=? AND revision=?", (shot_id, expected_revision)).fetchone()
            old = json.loads(old_revision["manifest_json"])
            keys, affected = self._changed(old, manifest)
            old_pins = json.loads(old_revision["resolved_registry_json"])
            if old_pins != resolved:
                keys.append("registry_versions")
                changed_ids = {key for key in old_pins.keys() | resolved.keys() if old_pins.get(key) != resolved.get(key)}
                for asset_id in changed_ids:
                    kind = registry[asset_id]["kind"] if asset_id in registry else None
                    affected.update(("face", "audio", "render", "sound") if kind in ("dialogue", "voice_profile") else SUBSYSTEMS)
            if "camera_intent" in keys:
                view_rows = conn.execute("SELECT DISTINCT subsystem FROM simulation_cache_versions WHERE shot_id=? AND validity='CURRENT' AND json_extract(source_json,'$.view_lod_dependency')=1", (shot_id,)).fetchall()
                affected.update(row["subsystem"] for row in view_rows)
            artifacts = [dict(r) for r in conn.execute("SELECT id,role,version,validity FROM artifact_versions WHERE shot_id=? AND validity='CURRENT'", (shot_id,)) if r["role"] in affected]
            media_impact = {table: conn.execute(f"SELECT COUNT(*) FROM {table} WHERE shot_id=? AND validity='CURRENT'", (shot_id,)).fetchone()[0] for table in ("video_reference_packages", "video_takes", "picture_locks", "sound_stems")} if keys else {}
            if keys:
                media_impact["delivery_packages"] = conn.execute("SELECT COUNT(*) FROM delivery_packages WHERE episode_id=? AND state!='STALE'", (shot["episode_id"],)).fetchone()[0]
            return {"changed_fields": sorted(keys), "affected_subsystems": sorted(affected), "stale_artifacts": artifacts, "media_impact": media_impact, "validation": validation, "new_input_hash": digest({"manifest": manifest, "registry": resolved})}

    def revise_shot(self, shot_id: str, expected_revision: int, manifest: dict,
                    require_auto_unlocked: bool = False, expected_input_hash: str | None = None,
                    after_write=None) -> dict:
        preview = self.preview_revision(shot_id, expected_revision, manifest)
        if not preview["changed_fields"]:
            raise DomainError("manifest unchanged")
        with self.db.transaction() as conn:
            shot = require(conn.execute("SELECT * FROM shots WHERE id=?", (shot_id,)).fetchone(), "shot")
            if shot["current_revision"] != expected_revision:
                raise DomainError("shot revision conflict", 409)
            version = expected_revision + 1
            old_manifest = json.loads(conn.execute("SELECT manifest_json FROM shot_revisions WHERE shot_id=? AND revision=?",
                                                   (shot_id, expected_revision)).fetchone()[0])
            registry = self._registry_map(conn, shot["project_id"])
            resolved = self._pin_registry(manifest, registry)
            if expected_input_hash is not None and digest({"manifest": manifest, "registry": resolved}) != expected_input_hash:
                raise DomainError("registry version changed before planning apply", 409)
            if require_auto_unlocked:
                protected = [row["subsystem"] for row in conn.execute(
                    "SELECT subsystem FROM subsystem_states WHERE shot_id=? AND subsystem IN (" + ",".join("?" for _ in preview["affected_subsystems"]) + ") AND (mode='MANUAL' OR lock_state='LOCKED' OR (observed_fingerprint IS NOT NULL AND observed_fingerprint!=COALESCE(synced_fingerprint,'')))",
                    (shot_id, *preview["affected_subsystems"]))] if preview["affected_subsystems"] else []
                if protected:
                    raise DomainError("planning apply blocked by MANUAL/LOCKED or external edit: " + ", ".join(protected), 409)
            revision_id = uid()
            conn.execute("INSERT INTO shot_revisions(id,shot_id,revision,manifest_json,resolved_registry_json,input_hash,created_at) VALUES(?,?,?,?,?,?,?)", (revision_id, shot_id, version, canonical(manifest), canonical(resolved), digest({"manifest": manifest, "registry": resolved}), now()))
            self._link_revision_assets(conn, revision_id, resolved)
            conn.execute("UPDATE shots SET current_revision=? WHERE id=?", (version, shot_id))
            for subsystem in preview["affected_subsystems"]:
                conn.execute("UPDATE subsystem_states SET validity='STALE',review='NEEDS_REVIEW',state_revision=state_revision+1 WHERE shot_id=? AND subsystem=?", (shot_id, subsystem))
                conn.execute("UPDATE artifact_versions SET validity='STALE' WHERE shot_id=? AND role=?", (shot_id, subsystem))
                if subsystem in ("hair", "cloth", "vfx"):
                    conn.execute("UPDATE simulation_cache_versions SET validity='STALE' WHERE shot_id=? AND subsystem=?", (shot_id, subsystem))
            new_performances = {p.get("performance_id"): p for p in manifest["performances"]}
            for old_performance in old_manifest["performances"]:
                pid = old_performance.get("performance_id")
                replacement = new_performances.get(pid)
                if pid and (replacement is None or any(old_performance.get(key) != replacement.get(key) for key in ("character_id", "dialogue_id", "voice_profile_id"))):
                    conn.execute("UPDATE voice_takes SET validity='STALE' WHERE shot_id=? AND performance_id=?", (shot_id, pid))
            conn.execute("UPDATE timing_revisions SET validity='STALE',review='NEEDS_REVIEW' WHERE shot_id=?", (shot_id,))
            conn.execute("UPDATE video_reference_packages SET validity='STALE' WHERE shot_id=?", (shot_id,))
            conn.execute("UPDATE video_takes SET validity='STALE' WHERE shot_id=?", (shot_id,))
            conn.execute("UPDATE picture_locks SET validity='STALE' WHERE shot_id=?", (shot_id,))
            conn.execute("UPDATE sound_stems SET validity='STALE' WHERE shot_id=?", (shot_id,))
            conn.execute("UPDATE delivery_packages SET state='STALE' WHERE episode_id=? AND state!='STALE'", (shot["episode_id"],))
            self.audit(conn, shot["project_id"], "shot.revised", shot_id, {"revision": version, "impact": preview})
            if after_write is not None:
                after_write(conn, shot_id, version)
            return self._shot(conn, require(conn.execute("SELECT * FROM shots WHERE id=?", (shot_id,)).fetchone()))

    def approve(self, shot_id: str, exact_revision: int, expected_current_revision: int, route: str) -> dict:
        if route not in ("UNREAL", "SEEDANCE", "HYBRID"):
            raise DomainError("invalid approved route")
        with self.db.transaction() as conn:
            shot = require(conn.execute("SELECT * FROM shots WHERE id=?", (shot_id,)).fetchone(), "shot")
            if shot["current_revision"] != expected_current_revision or exact_revision != shot["current_revision"]:
                raise DomainError("approval requires exact current revision", 409)
            item = {"id": uid(), "project_id": shot["project_id"], "scope": "plan", "scope_id": shot_id, "exact_version": exact_revision, "decision_json": canonical({"approved_route": route}), "actor": "local_user", "created_at": now()}
            conn.execute("INSERT INTO approvals(id,project_id,scope,scope_id,exact_version,decision_json,actor,created_at) VALUES(:id,:project_id,:scope,:scope_id,:exact_version,:decision_json,:actor,:created_at)", item)
            self.audit(conn, shot["project_id"], "plan.approved", shot_id, {"revision": exact_revision, "route": route})
            item["decision"] = json.loads(item.pop("decision_json"))
            return item

    def set_state(self, shot_id: str, subsystem: str, expected_state_revision: int, mode: str, lock_state: str, adopt_fingerprint: str | None = None) -> dict:
        if subsystem not in SUBSYSTEMS or mode not in ("AUTO", "MANUAL") or lock_state not in ("UNLOCKED", "LOCKED"):
            raise DomainError("invalid subsystem state")
        with self.db.transaction() as conn:
            shot = require(conn.execute("SELECT * FROM shots WHERE id=?", (shot_id,)).fetchone(), "shot")
            state = require(conn.execute("SELECT * FROM subsystem_states WHERE shot_id=? AND subsystem=?", (shot_id, subsystem)).fetchone(), "subsystem")
            if state["state_revision"] != expected_state_revision:
                raise DomainError("subsystem state revision conflict", 409)
            if adopt_fingerprint is not None and (mode != "MANUAL" or lock_state != "LOCKED" or not adopt_fingerprint):
                raise DomainError("adopt requires MANUAL + LOCKED and a fingerprint")
            if adopt_fingerprint and state["observed_fingerprint"] and adopt_fingerprint != state["observed_fingerprint"]:
                raise DomainError("adopt fingerprint differs from observed external content", 409)
            if mode == "AUTO" and state["observed_fingerprint"] and state["observed_fingerprint"] != state["synced_fingerprint"]:
                raise DomainError("external edit conflict; adopt and lock before automation", 409)
            synced = adopt_fingerprint or state["synced_fingerprint"]
            conn.execute("UPDATE subsystem_states SET mode=?,lock_state=?,synced_fingerprint=?,state_revision=state_revision+1 WHERE shot_id=? AND subsystem=?", (mode, lock_state, synced, shot_id, subsystem))
            self.audit(conn, shot["project_id"], "subsystem.changed", shot_id, {"subsystem": subsystem, "mode": mode, "lock": lock_state, "adopted": bool(adopt_fingerprint)})
            return require(conn.execute("SELECT * FROM subsystem_states WHERE shot_id=? AND subsystem=?", (shot_id, subsystem)).fetchone())

    def observe_external(self, shot_id: str, subsystem: str, expected_state_revision: int, fingerprint: str) -> dict:
        if subsystem not in SUBSYSTEMS or not fingerprint:
            raise DomainError("subsystem and fingerprint required")
        with self.db.transaction() as conn:
            shot = require(conn.execute("SELECT * FROM shots WHERE id=?", (shot_id,)).fetchone(), "shot")
            state = require(conn.execute("SELECT * FROM subsystem_states WHERE shot_id=? AND subsystem=?", (shot_id, subsystem)).fetchone(), "subsystem")
            if state["state_revision"] != expected_state_revision:
                raise DomainError("subsystem state revision conflict", 409)
            conflict = state["synced_fingerprint"] is not None and state["synced_fingerprint"] != fingerprint
            conn.execute("UPDATE subsystem_states SET observed_fingerprint=?,validity=?,review=?,state_revision=state_revision+1 WHERE shot_id=? AND subsystem=?", (fingerprint, "STALE" if conflict else state["validity"], "NEEDS_REVIEW" if conflict else state["review"], shot_id, subsystem))
            self.audit(conn, shot["project_id"], "subsystem.external_observed", shot_id, {"subsystem": subsystem, "conflict": conflict})
            return {"conflict": conflict, "state": require(conn.execute("SELECT * FROM subsystem_states WHERE shot_id=? AND subsystem=?", (shot_id, subsystem)).fetchone())}

    def approve_subsystem(self, shot_id: str, subsystem: str, exact_shot_revision: int, expected_state_revision: int) -> dict:
        if subsystem not in SUBSYSTEMS:
            raise DomainError("unknown subsystem")
        with self.db.transaction() as conn:
            shot = require(conn.execute("SELECT * FROM shots WHERE id=?", (shot_id,)).fetchone(), "shot")
            state = require(conn.execute("SELECT * FROM subsystem_states WHERE shot_id=? AND subsystem=?", (shot_id, subsystem)).fetchone(), "subsystem")
            if shot["current_revision"] != exact_shot_revision or state["state_revision"] != expected_state_revision:
                raise DomainError("approval requires exact shot and state revision", 409)
            if state["observed_fingerprint"] and state["observed_fingerprint"] != state["synced_fingerprint"]:
                raise DomainError("external edit conflict must be adopted first", 409)
            if state["validity"] == "STALE" and not (state["mode"] == "MANUAL" and state["lock_state"] == "LOCKED" and state["synced_fingerprint"]):
                raise DomainError("stale automation output requires rebuild", 409)
            conn.execute("UPDATE subsystem_states SET validity='CURRENT',review='APPROVED',state_revision=state_revision+1 WHERE shot_id=? AND subsystem=?", (shot_id, subsystem))
            item = {"id": uid(), "project_id": shot["project_id"], "scope": f"subsystem:{subsystem}", "scope_id": shot_id, "exact_version": expected_state_revision, "decision_json": canonical({"shot_revision": exact_shot_revision, "fingerprint": state["synced_fingerprint"]}), "actor": "local_user", "created_at": now()}
            conn.execute("INSERT INTO approvals(id,project_id,scope,scope_id,exact_version,decision_json,actor,created_at) VALUES(:id,:project_id,:scope,:scope_id,:exact_version,:decision_json,:actor,:created_at)", item)
            self.audit(conn, shot["project_id"], "subsystem.approved", shot_id, {"subsystem": subsystem, "shot_revision": exact_shot_revision, "state_revision": expected_state_revision})
            return require(conn.execute("SELECT * FROM subsystem_states WHERE shot_id=? AND subsystem=?", (shot_id, subsystem)).fetchone())

    def preflight(self, shot_id: str, revision: int, subsystem: str, depends_on: list[str] | None = None) -> dict:
        if subsystem not in SUBSYSTEMS:
            raise DomainError("unknown subsystem")
        depends_on = depends_on or []
        with self.db.connect() as conn:
            shot = require(conn.execute("SELECT * FROM shots WHERE id=?", (shot_id,)).fetchone(), "shot")
            blockers = []
            if shot["current_revision"] != revision:
                blockers.append("shot revision changed")
            approval = conn.execute("SELECT decision_json FROM approvals WHERE project_id=? AND scope='plan' AND scope_id=? AND exact_version=?", (shot["project_id"], shot_id, revision)).fetchone()
            if approval is None:
                blockers.append("exact plan approval missing")
            pinned_row = conn.execute("SELECT resolved_registry_json FROM shot_revisions WHERE shot_id=? AND revision=?", (shot_id, revision)).fetchone()
            if pinned_row:
                current = self._registry_map(conn, shot["project_id"])
                pinned_registry = json.loads(pinned_row["resolved_registry_json"])
                if not pinned_registry:
                    blockers.append("legacy revision has no pinned registry; create new revision")
                for asset_id, pinned in pinned_registry.items():
                    if asset_id not in current or current[asset_id]["id"] != pinned["asset_version_id"]:
                        blockers.append(f"registry {asset_id} changed; create and approve new revision")
            state = require(conn.execute("SELECT * FROM subsystem_states WHERE shot_id=? AND subsystem=?", (shot_id, subsystem)).fetchone())
            if state["mode"] != "AUTO" or state["lock_state"] != "UNLOCKED":
                blockers.append("manual or locked subsystem")
            if state["observed_fingerprint"] and state["observed_fingerprint"] != state["synced_fingerprint"]:
                blockers.append("external edit fingerprint conflict")
            waiting = []
            for dependency_id in depends_on:
                dep = conn.execute("SELECT state,project_id FROM jobs WHERE id=?", (dependency_id,)).fetchone()
                if dep is None or dep["project_id"] != shot["project_id"] or dep["state"] in ("BLOCKED", "RECONCILING", "FAILED", "CANCELLED"):
                    blockers.append(f"dependency {dependency_id} unavailable in project")
                elif dep["state"] != "COMPLETED":
                    waiting.append(dependency_id)
            return {"allowed": not blockers, "blockers": blockers, "waiting_dependencies": waiting, "shot_revision": revision, "state_revision": state["state_revision"], "adapter_kind": self.adapter.kind, "approved_route": json.loads(approval[0])["approved_route"] if approval else None}

    def enqueue(self, shot_id: str, revision: int, subsystem: str, profile_version: str, depends_on: list[str] | None = None, resource: str = "GPU_HEAVY") -> dict:
        depends_on = depends_on or []
        if not profile_version or resource not in ("GPU_HEAVY", "CPU"):
            raise DomainError("profile_version and known resource required")
        with self.db.transaction() as conn:
            preflight = self.preflight(shot_id, revision, subsystem, depends_on)
            if not preflight["allowed"]:
                raise DomainError("; ".join(preflight["blockers"]), 409)
            shot = require(conn.execute("SELECT * FROM shots WHERE id=?", (shot_id,)).fetchone())
            input_hash = conn.execute("SELECT input_hash FROM shot_revisions WHERE shot_id=? AND revision=?", (shot_id, revision)).fetchone()[0]
            operation_id = digest({"project": shot["project_id"], "shot": shot_id, "revision": revision, "subsystem": subsystem, "input_hash": input_hash, "profile_version": profile_version})
            previous = conn.execute("SELECT * FROM jobs WHERE operation_id=?", (operation_id,)).fetchone()
            if previous:
                old_dependencies = {r[0] for r in conn.execute("SELECT depends_on_id FROM job_dependencies WHERE job_id=?", (previous["id"],))}
                if old_dependencies != set(depends_on) or previous["resource"] != resource:
                    raise DomainError("same operation ID has different dependencies or resource", 409)
                return dict(previous)
            timestamp = now()
            item = {"id": uid(), "project_id": shot["project_id"], "shot_id": shot_id, "shot_revision": revision, "subsystem": subsystem, "operation_id": operation_id, "input_hash": input_hash, "profile_version": profile_version, "state": "QUEUED", "expected_state_revision": preflight["state_revision"], "provider_request_id": None, "resource": resource, "adapter_kind": self.adapter.kind, "error": None, "created_at": timestamp, "updated_at": timestamp}
            conn.execute("INSERT INTO jobs(id,project_id,shot_id,shot_revision,subsystem,operation_id,input_hash,profile_version,state,expected_state_revision,provider_request_id,resource,adapter_kind,error,created_at,updated_at) VALUES(:id,:project_id,:shot_id,:shot_revision,:subsystem,:operation_id,:input_hash,:profile_version,:state,:expected_state_revision,:provider_request_id,:resource,:adapter_kind,:error,:created_at,:updated_at)", item)
            for dependency_id in depends_on:
                conn.execute("INSERT INTO job_dependencies(job_id,depends_on_id) VALUES(?,?)", (item["id"], dependency_id))
            self.audit(conn, shot["project_id"], "job.queued", item["id"], {"operation_id": operation_id, "adapter": self.adapter.kind})
            return item

    def jobs(self, project_id: str) -> list[dict]:
        self.project(project_id)
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM jobs WHERE project_id=? ORDER BY created_at DESC", (project_id,))]

    def job(self, job_id: str) -> dict:
        with self.db.connect() as conn:
            item = require(conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone(), "job")
            item["attempts"] = [dict(r) for r in conn.execute("SELECT * FROM job_attempts WHERE job_id=? ORDER BY attempt", (job_id,))]
            item["receipt"] = row_dict(conn.execute("SELECT * FROM receipts WHERE operation_id=?", (item["operation_id"],)).fetchone())
            if item["receipt"]:
                item["receipt"]["output"] = json.loads(item["receipt"].pop("output_json"))
            return item

    def _prewrite_blocker(self, conn, job: dict) -> str | None:
        if conn.execute("SELECT 1 FROM job_cancellations WHERE job_id=?", (job['id'],)).fetchone():
            return "cancel requested; worker must stop before lease release"
        shot = conn.execute("SELECT current_revision FROM shots WHERE id=?", (job["shot_id"],)).fetchone()
        state = conn.execute("SELECT * FROM subsystem_states WHERE shot_id=? AND subsystem=?", (job["shot_id"], job["subsystem"])).fetchone()
        if shot["current_revision"] != job["shot_revision"]:
            return "shot revision changed after enqueue"
        pinned = conn.execute("SELECT resolved_registry_json FROM shot_revisions WHERE shot_id=? AND revision=?", (job["shot_id"], job["shot_revision"])).fetchone()
        current = self._registry_map(conn, job["project_id"])
        if pinned and any(asset_id not in current or current[asset_id]["id"] != asset["asset_version_id"] for asset_id, asset in json.loads(pinned[0]).items()):
            return "registry version changed after enqueue"
        if state["state_revision"] != job["expected_state_revision"] or state["mode"] != "AUTO" or state["lock_state"] != "UNLOCKED":
            return "subsystem revision/mode/lock changed after enqueue"
        if state["observed_fingerprint"] and state["observed_fingerprint"] != state["synced_fingerprint"]:
            return "external edit fingerprint conflict before write"
        if conn.execute("SELECT 1 FROM job_dependencies d JOIN jobs p ON p.id=d.depends_on_id WHERE d.job_id=? AND p.state!='COMPLETED'", (job["id"],)).fetchone():
            return "dependency no longer complete"
        return None

    def process_next(self) -> dict | None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE jobs SET state='BLOCKED',error='dependency failed or requires reconcile',updated_at=? WHERE state='QUEUED' AND EXISTS (SELECT 1 FROM job_dependencies d JOIN jobs p ON p.id=d.depends_on_id WHERE d.job_id=jobs.id AND p.state IN ('BLOCKED','RECONCILING','FAILED','CANCELLED'))", (now(),))
            rows = conn.execute("SELECT * FROM jobs WHERE state='QUEUED' ORDER BY created_at").fetchall()
            chosen = None
            for row in rows:
                job = dict(row)
                if conn.execute("SELECT 1 FROM job_dependencies d JOIN jobs p ON p.id=d.depends_on_id WHERE d.job_id=? AND p.state!='COMPLETED'", (job["id"],)).fetchone():
                    continue
                if conn.execute("SELECT 1 FROM resource_leases WHERE resource=?", (job["resource"],)).fetchone():
                    continue
                if job["resource"] == "GPU_HEAVY" and gpu_queue.holder(conn):
                    continue
                chosen = job
                break
            if not chosen:
                return None
            blocker = self._prewrite_blocker(conn, chosen)
            if blocker:
                conn.execute("UPDATE jobs SET state='BLOCKED',error=?,updated_at=? WHERE id=?", (blocker, now(), chosen["id"]))
                self.audit(conn, chosen["project_id"], "job.blocked", chosen["id"], {"reason": blocker})
            else:
                attempt = conn.execute("SELECT COALESCE(MAX(attempt),0)+1 FROM job_attempts WHERE job_id=?", (chosen["id"],)).fetchone()[0]
                attempt_id = uid()
                conn.execute("INSERT INTO resource_leases(resource,job_id,acquired_at) VALUES(?,?,?)", (chosen["resource"], chosen["id"], now()))
                if chosen["resource"] == "GPU_HEAVY":
                    gpu_queue.try_acquire(conn, chosen["operation_id"], "JOB")
                conn.execute("INSERT INTO job_attempts(id,job_id,attempt,state,started_at) VALUES(?,?,?,'RUNNING',?)", (attempt_id, chosen["id"], attempt, now()))
                conn.execute("UPDATE jobs SET state='RUNNING',updated_at=? WHERE id=?", (now(), chosen["id"]))
        if blocker:
            return self.job(chosen["id"])
        try:
            output = self.adapter.apply(chosen)
            if any(output.get(key) != chosen[key] for key in ("operation_id", "input_hash", "shot_id", "shot_revision", "subsystem")) or output.get("adapter_kind") != self.adapter.kind:
                raise DomainError("fake adapter receipt failed expected-output validation", 409)
            with self.db.transaction() as conn:
                blocker = self._prewrite_blocker(conn, chosen)
                if blocker:
                    raise DomainError(blocker, 409)
                receipt_id = uid()
                conn.execute("INSERT INTO receipts(id,operation_id,input_hash,status,output_json,created_at) VALUES(?,?,?,'COMPLETED',?,?)", (receipt_id, chosen["operation_id"], chosen["input_hash"], canonical(output), now()))
                version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM artifact_versions WHERE shot_id=? AND role=?", (chosen["shot_id"], chosen["subsystem"])).fetchone()[0]
                artifact_id = uid()
                conn.execute("INSERT INTO artifact_versions(id,project_id,shot_id,role,version,input_hash,relative_path,provenance,validity,created_at) VALUES(?,?,?,?,?,?,NULL,?,'CURRENT',?)", (artifact_id, chosen["project_id"], chosen["shot_id"], chosen["subsystem"], version, chosen["input_hash"], self.adapter.kind, now()))
                conn.execute("INSERT INTO job_artifacts(job_id,artifact_id,role) VALUES(?,?,'output')", (chosen["id"], artifact_id))
                conn.execute("UPDATE jobs SET state='COMPLETED',updated_at=? WHERE id=?", (now(), chosen["id"]))
                conn.execute("UPDATE job_attempts SET state='COMPLETED',ended_at=?,receipt_id=? WHERE id=?", (now(), receipt_id, attempt_id))
                conn.execute("DELETE FROM resource_leases WHERE resource=? AND job_id=?", (chosen["resource"], chosen["id"]))
                gpu_queue.release(conn, chosen["operation_id"])
                self.audit(conn, chosen["project_id"], "job.completed", chosen["id"], {"receipt_id": receipt_id, "adapter": self.adapter.kind})
        except Exception as exc:
            safe_error = f"{type(exc).__name__}; outcome requires reconcile"
            with self.db.transaction() as conn:
                cancelled = conn.execute("SELECT 1 FROM job_cancellations WHERE job_id=?", (chosen['id'],)).fetchone()
                safe_cancel = cancelled and 'output' in locals() and output.get('external_writes') == 0 and self.adapter.kind == 'FAKE_CONTROL_FLOW'
                end_state = 'CANCELLED' if safe_cancel else 'RECONCILING'
                conn.execute("UPDATE jobs SET state=?,error=?,updated_at=? WHERE id=?", (end_state, safe_error, now(), chosen["id"]))
                conn.execute("UPDATE job_attempts SET state=?,ended_at=? WHERE id=?", (end_state, now(), attempt_id))
                conn.execute("DELETE FROM resource_leases WHERE resource=? AND job_id=?", (chosen["resource"], chosen["id"]))
                gpu_queue.release(conn, chosen["operation_id"])
                self.audit(conn, chosen["project_id"], "job.reconciling", chosen["id"], {"reason": safe_error})
        return self.job(chosen["id"])

    def cancel(self, job_id: str) -> dict:
        with self.db.transaction() as conn:
            job = require(conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone(), 'job')
            if job['adapter_kind'] != 'FAKE_CONTROL_FLOW':
                raise DomainError('Chưa có cơ chế dừng/đối soát worker integration; không giả đã hủy.', 409)
            if job['state'] in ('CANCELLED', 'CANCEL_REQUESTED'):
                return self.job(job_id)
            if job['state'] not in ('QUEUED', 'RUNNING'):
                raise DomainError('Chỉ hủy job đang chờ/chạy; kết quả chưa rõ cần đối soát.', 409)
            conn.execute('INSERT OR IGNORE INTO job_cancellations VALUES(?,?)', (job_id, now()))
            state = 'CANCELLED' if job['state'] == 'QUEUED' else 'CANCEL_REQUESTED'
            conn.execute('UPDATE jobs SET state=?,updated_at=? WHERE id=?', (state, now(), job_id))
            self.audit(conn, job['project_id'], 'job.cancel.requested', job_id, {'state': state, 'side_effects_undone': False})
        return self.job(job_id)

    def reconcile(self, job_id: str) -> dict:
        with self.db.transaction() as conn:
            job = require(conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone(), "job")
            if job["state"] != "RECONCILING":
                raise DomainError("job is not reconciling", 409)
            receipt = conn.execute("SELECT * FROM receipts WHERE operation_id=?", (job["operation_id"],)).fetchone()
            if receipt and receipt["input_hash"] == job["input_hash"]:
                output = json.loads(receipt["output_json"])
                if all(output.get(key) == job[key] for key in ("operation_id", "input_hash", "shot_id", "shot_revision", "subsystem")):
                    conn.execute("UPDATE jobs SET state='COMPLETED',error=NULL,updated_at=? WHERE id=?", (now(), job_id))
                else:
                    conn.execute("UPDATE jobs SET error='receipt output contract mismatch; manual reconcile required',updated_at=? WHERE id=?", (now(), job_id))
            else:
                conn.execute("UPDATE jobs SET error='outcome unknown; manual reconcile required; no automatic resubmit',updated_at=? WHERE id=?", (now(), job_id))
            self.audit(conn, job["project_id"], "job.reconciled", job_id, {"receipt_found": bool(receipt)})
        return self.job(job_id)

    def retry(self, job_id: str) -> dict:
        with self.db.transaction() as conn:
            job = require(conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone(), "job")
            if job["state"] != "BLOCKED" or job["provider_request_id"] or conn.execute("SELECT 1 FROM receipts WHERE operation_id=?", (job["operation_id"],)).fetchone():
                raise DomainError("retry requires a known pre-write block and no provider request or receipt", 409)
            preflight = self.preflight(job["shot_id"], job["shot_revision"], job["subsystem"], [r[0] for r in conn.execute("SELECT depends_on_id FROM job_dependencies WHERE job_id=?", (job_id,))])
            if not preflight["allowed"]:
                raise DomainError("; ".join(preflight["blockers"]), 409)
            conn.execute("UPDATE jobs SET state='QUEUED',expected_state_revision=?,error=NULL,updated_at=? WHERE id=?", (preflight["state_revision"], now(), job_id))
            self.audit(conn, job["project_id"], "job.retry_requested", job_id, {"operation_id": job["operation_id"]})
        return self.job(job_id)

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
