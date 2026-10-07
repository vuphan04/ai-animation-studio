"""Durable local voice takes and exact, human-approved timing revisions."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

from .domain import DomainError, canonical, digest
from .p3_domain import CameraAdapter, FacialAdapter, duration_frames, resolve_camera_intent
from .p2 import actor_path
from .service import now, require, uid
from .voice_adapter import LocalFixtureVoiceProvider, inspect_wav


class PerformanceService:
    def __init__(self, studio):
        self.studio = studio
        self.db = studio.db
        self.provider = LocalFixtureVoiceProvider()

    def _mapping(self, conn, shot_id: str, performance_id: str) -> dict:
        shot = require(conn.execute("SELECT * FROM shots WHERE id=?", (shot_id,)).fetchone(), "shot")
        rev = require(conn.execute("SELECT * FROM shot_revisions WHERE shot_id=? AND revision=?",
                                   (shot_id, shot["current_revision"])).fetchone(), "shot revision")
        manifest = json.loads(rev["manifest_json"])
        matches = [p for p in manifest["performances"] if p.get("performance_id") == performance_id]
        if len(matches) != 1 or not matches[0].get("dialogue_id") or not matches[0].get("voice_profile_id"):
            raise DomainError("Performance cần ID, dialogue và voice profile đã đăng ký.", 409)
        approval = conn.execute("SELECT 1 FROM approvals WHERE project_id=? AND scope='plan' AND scope_id=? AND exact_version=?",
                                (shot["project_id"], shot_id, shot["current_revision"])).fetchone()
        if not approval:
            raise DomainError("Cần duyệt đúng shot revision trước khi tạo voice.", 409)
        pinned = json.loads(rev["resolved_registry_json"])
        assets = {}
        for key, kind in (("dialogue_id", "dialogue"), ("voice_profile_id", "voice_profile"), ("character_id", "character")):
            registry_id = matches[0][key]
            if registry_id not in pinned:
                raise DomainError("Manifest thiếu registry pin cho voice.", 409)
            asset = require(conn.execute("SELECT * FROM asset_versions WHERE id=? AND project_id=?",
                                         (pinned[registry_id]["asset_version_id"], shot["project_id"])).fetchone(), "asset version")
            latest = conn.execute("SELECT MAX(version) FROM asset_versions WHERE project_id=? AND registry_id=?",
                                  (shot["project_id"], registry_id)).fetchone()[0]
            if asset["kind"] != kind or asset["version"] != latest:
                raise DomainError("Registry version đã đổi; tạo và duyệt shot revision mới.", 409)
            assets[key] = {**dict(asset), "metadata": json.loads(asset["metadata_json"])}
        dialogue = assets["dialogue_id"]["metadata"]
        if dialogue.get("character_id") != matches[0]["character_id"] or not dialogue.get("text"):
            raise DomainError("Dialogue không khớp nhân vật hoặc thiếu text.", 409)
        return {"shot": dict(shot), "manifest": manifest, "performance": matches[0], "assets": assets}

    def enqueue_voice(self, shot_id: str, performance_id: str, client_request_id: str) -> dict:
        try:
            uuid.UUID(client_request_id)
        except (ValueError, AttributeError) as exc:
            raise DomainError("client_request_id phải là UUID để tránh nhân đôi take.") from exc
        with self.db.transaction() as conn:
            mapping = self._mapping(conn, shot_id, performance_id)
            shot, performance, assets = mapping["shot"], mapping["performance"], mapping["assets"]
            state = require(conn.execute("SELECT * FROM subsystem_states WHERE shot_id=? AND subsystem='audio'", (shot_id,)).fetchone(), "audio state")
            if state["mode"] != "AUTO" or state["lock_state"] != "UNLOCKED" or (state["observed_fingerprint"] and state["observed_fingerprint"] != state["synced_fingerprint"]):
                raise DomainError("Audio đang manual/locked hoặc có chỉnh sửa ngoài app.", 409)
            profile = assets["voice_profile_id"]["metadata"]
            if profile.get("provider") != self.provider.kind:
                raise DomainError("Provider live chưa có API/version, key và ngân sách được duyệt; không gửi request.", 409)
            sample_rate, fixture_ms = profile.get("sample_rate"), profile.get("fixture_duration_ms")
            if type(sample_rate) is not int or not 8000 <= sample_rate <= 48000 or type(fixture_ms) is not int or not 50 <= fixture_ms <= 60000:
                raise DomainError("Voice fixture cần sample_rate 8000–48000 và duration 50–60000 ms.")
            request = {"schema_version": "p3.voice-request.1", "shot_id": shot_id, "shot_revision": shot["current_revision"],
                       "performance_id": performance_id, "character_id": performance["character_id"],
                       "dialogue_id": performance["dialogue_id"], "dialogue_version_id": assets["dialogue_id"]["id"],
                       "dialogue_text": assets["dialogue_id"]["metadata"]["text"],
                       "voice_profile_id": performance["voice_profile_id"], "voice_profile_version_id": assets["voice_profile_id"]["id"],
                       "provider_kind": self.provider.kind, "sample_rate": sample_rate, "fixture_duration_ms": fixture_ms}
            input_hash = digest(request)
            operation_id = digest({"input_hash": input_hash, "client_request_id": client_request_id})
            existing = conn.execute("SELECT * FROM voice_jobs WHERE operation_id=?", (operation_id,)).fetchone()
            if existing:
                return self._job(conn, existing)
            request["operation_id"] = operation_id
            timestamp = now()
            item = {"id": uid(), "project_id": shot["project_id"], "shot_id": shot_id, "shot_revision": shot["current_revision"],
                    "performance_id": performance_id, "operation_id": operation_id, "input_hash": input_hash,
                    "request_json": canonical(request), "state": "QUEUED", "attempt": 0, "provider_request_id": None,
                    "error": None, "created_at": timestamp, "updated_at": timestamp}
            conn.execute("INSERT INTO voice_jobs(id,project_id,shot_id,shot_revision,performance_id,operation_id,input_hash,request_json,state,attempt,provider_request_id,error,created_at,updated_at) VALUES(:id,:project_id,:shot_id,:shot_revision,:performance_id,:operation_id,:input_hash,:request_json,:state,:attempt,:provider_request_id,:error,:created_at,:updated_at)", item)
            self.studio.audit(conn, shot["project_id"], "voice.queued", item["id"], {"operation_id": operation_id, "adapter": self.provider.kind})
            return self._job(conn, item)

    def _job(self, conn, row) -> dict:
        item = dict(row)
        item.pop("request_json", None)
        return item

    def jobs(self, project_id: str) -> list[dict]:
        self.studio.project(project_id)
        with self.db.connect() as conn:
            return [self._job(conn, row) for row in conn.execute("SELECT * FROM voice_jobs WHERE project_id=? ORDER BY created_at DESC", (project_id,))]

    def job(self, job_id: str) -> dict:
        with self.db.connect() as conn:
            row = require(conn.execute("SELECT * FROM voice_jobs WHERE id=?", (job_id,)).fetchone(), "voice job")
            result = self._job(conn, row)
            result["attempts"] = [dict(a) for a in conn.execute("SELECT * FROM voice_job_attempts WHERE job_id=? ORDER BY attempt", (job_id,))]
            return result

    def _prewrite(self, conn, job: dict, request: dict) -> None:
        mapping = self._mapping(conn, job["shot_id"], job["performance_id"])
        assets = mapping["assets"]
        state = require(conn.execute("SELECT * FROM subsystem_states WHERE shot_id=? AND subsystem='audio'", (job["shot_id"],)).fetchone(), "audio state")
        if mapping["shot"]["current_revision"] != job["shot_revision"] or assets["dialogue_id"]["id"] != request["dialogue_version_id"] or assets["voice_profile_id"]["id"] != request["voice_profile_version_id"]:
            raise DomainError("Voice input/revision đã đổi sau khi xếp việc.", 409)
        if state["mode"] != "AUTO" or state["lock_state"] != "UNLOCKED" or (state["observed_fingerprint"] and state["observed_fingerprint"] != state["synced_fingerprint"]):
            raise DomainError("Audio bị khóa hoặc chỉnh ngoài app trước khi ghi.", 409)

    def _voice_path(self, job: dict) -> tuple[Path, str]:
        project = self.studio.project(job["project_id"])
        relative = f"audio/dialogue/{job['shot_id']}/{job['performance_id']}/{job['operation_id']}.wav"
        path = Path(project["root"]) / relative
        return path, relative

    def _complete(self, conn, job: dict, request: dict, receipt: dict, relative: str, attempt_id: str | None) -> dict:
        previous = conn.execute("SELECT * FROM voice_takes WHERE job_id=?", (job["id"],)).fetchone()
        if previous:
            if previous["wav_sha256"] != receipt["wav_sha256"]:
                raise DomainError("Voice receipt xung đột với take đã lưu.", 409)
            take = dict(previous)
        else:
            version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM voice_takes WHERE shot_id=? AND performance_id=?",
                                   (job["shot_id"], job["performance_id"])).fetchone()[0]
            current_revision = conn.execute("SELECT current_revision FROM shots WHERE id=?", (job["shot_id"],)).fetchone()[0]
            latest_dialogue = conn.execute("SELECT id FROM asset_versions WHERE project_id=? AND registry_id=? ORDER BY version DESC LIMIT 1",
                                           (job["project_id"], request["dialogue_id"])).fetchone()[0]
            latest_profile = conn.execute("SELECT id FROM asset_versions WHERE project_id=? AND registry_id=? ORDER BY version DESC LIMIT 1",
                                          (job["project_id"], request["voice_profile_id"])).fetchone()[0]
            validity = "CURRENT" if (current_revision == job["shot_revision"] and latest_dialogue == request["dialogue_version_id"] and latest_profile == request["voice_profile_version_id"]) else "STALE"
            take = {"id": uid(), "job_id": job["id"], "project_id": job["project_id"], "shot_id": job["shot_id"],
                    "performance_id": job["performance_id"], "version": version,
                    "dialogue_version_id": request["dialogue_version_id"], "voice_profile_version_id": request["voice_profile_version_id"],
                    "provider_kind": self.provider.kind, "provider_request_id": receipt["provider_request_id"],
                    "relative_path": relative, "wav_sha256": receipt["wav_sha256"], "sample_rate": receipt["sample_rate"],
                    "sample_count": receipt["sample_count"], "review": "NEEDS_REVIEW", "lock_state": "UNLOCKED",
                    "validity": validity, "created_at": now()}
            conn.execute("INSERT INTO voice_takes(id,job_id,project_id,shot_id,performance_id,version,dialogue_version_id,voice_profile_version_id,provider_kind,provider_request_id,relative_path,wav_sha256,sample_rate,sample_count,review,lock_state,validity,created_at) VALUES(:id,:job_id,:project_id,:shot_id,:performance_id,:version,:dialogue_version_id,:voice_profile_version_id,:provider_kind,:provider_request_id,:relative_path,:wav_sha256,:sample_rate,:sample_count,:review,:lock_state,:validity,:created_at)", take)
        output = {"schema_version": "p3.voice-receipt.1", "operation_id": job["operation_id"],
                  "input_hash": job["input_hash"], "take_id": take["id"], "version": take["version"],
                  "relative_path": relative, **receipt}
        existing_receipt = conn.execute("SELECT input_hash,output_json FROM receipts WHERE operation_id=?", (job["operation_id"],)).fetchone()
        if existing_receipt and (existing_receipt["input_hash"] != job["input_hash"] or existing_receipt["output_json"] != canonical(output)):
            raise DomainError("Voice operation có receipt xung đột; cần đối soát thủ công.", 409)
        if not existing_receipt:
            conn.execute("INSERT INTO receipts(id,operation_id,input_hash,status,output_json,created_at) VALUES(?,?,?,'COMPLETED',?,?)",
                         (uid(), job["operation_id"], job["input_hash"], canonical(output), now()))
        conn.execute("UPDATE voice_jobs SET state='COMPLETED',provider_request_id=?,error=NULL,updated_at=? WHERE id=?",
                     (receipt["provider_request_id"], now(), job["id"]))
        if attempt_id:
            conn.execute("UPDATE voice_job_attempts SET state='COMPLETED',ended_at=? WHERE id=?", (now(), attempt_id))
        conn.execute("DELETE FROM voice_resource_lease WHERE resource='VOICE_LOCAL' AND job_id=?", (job["id"],))
        self.studio.audit(conn, job["project_id"], "voice.completed", take["id"], {"version": take["version"], "adapter": self.provider.kind})
        return take

    def process_next(self) -> dict | None:
        with self.db.transaction() as conn:
            if conn.execute("SELECT 1 FROM voice_resource_lease WHERE resource='VOICE_LOCAL'").fetchone():
                return None
            row = conn.execute("SELECT * FROM voice_jobs WHERE state='QUEUED' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            job = dict(row)
            attempt_id = uid()
            conn.execute("INSERT INTO voice_resource_lease(resource,job_id,acquired_at) VALUES('VOICE_LOCAL',?,?)", (job["id"], now()))
            conn.execute("INSERT INTO voice_job_attempts(id,job_id,attempt,state,started_at) VALUES(?,?,?,'RUNNING',?)",
                         (attempt_id, job["id"], job["attempt"] + 1, now()))
            conn.execute("UPDATE voice_jobs SET state='RUNNING',attempt=attempt+1,updated_at=? WHERE id=?", (now(), job["id"]))
        wrote = False
        try:
            request = json.loads(job["request_json"])
            with self.db.connect() as conn:
                self._prewrite(conn, job, request)
            data, receipt = self.provider.render(request)
            if inspect_wav(data) != (receipt["sample_rate"], receipt["sample_count"]):
                raise DomainError("Voice fixture WAV metadata không khớp receipt.", 409)
            path, relative = self._voice_path(job)
            path.parent.mkdir(parents=True, exist_ok=True)
            with self.db.transaction() as conn:
                self._prewrite(conn, job, request)
                if path.exists():
                    if hashlib.sha256(path.read_bytes()).hexdigest() != receipt["wav_sha256"]:
                        raise DomainError("Voice file ngoài app đã đổi; cần đối soát.", 409)
                else:
                    tmp = path.with_suffix(".part")
                    tmp.write_bytes(data)
                    os.replace(tmp, path)
                    wrote = True
                self._complete(conn, job, request, receipt, relative, attempt_id)
        except Exception as exc:
            state = "RECONCILING" if wrote else "BLOCKED"
            with self.db.transaction() as conn:
                conn.execute("UPDATE voice_jobs SET state=?,error=?,updated_at=? WHERE id=?",
                             (state, f"{type(exc).__name__}; {'inspect output' if wrote else 'prewrite blocked'}", now(), job["id"]))
                conn.execute("UPDATE voice_job_attempts SET state=?,ended_at=? WHERE id=?", (state, now(), attempt_id))
                conn.execute("DELETE FROM voice_resource_lease WHERE resource='VOICE_LOCAL' AND job_id=?", (job["id"],))
                self.studio.audit(conn, job["project_id"], "voice." + state.lower(), job["id"], {"reason": type(exc).__name__})
        return self.job(job["id"])

    def reconcile(self, job_id: str) -> dict:
        with self.db.connect() as conn:
            job = dict(require(conn.execute("SELECT * FROM voice_jobs WHERE id=?", (job_id,)).fetchone(), "voice job"))
        if job["state"] != "RECONCILING":
            raise DomainError("Voice job không cần đối soát.", 409)
        request = json.loads(job["request_json"])
        path, relative = self._voice_path(job)
        if not path.is_file():
            with self.db.transaction() as conn:
                if conn.execute("SELECT 1 FROM receipts WHERE operation_id=?", (job["operation_id"],)).fetchone() or job["provider_request_id"]:
                    raise DomainError("Chưa xác định được kết quả thao tác; cần kiểm tra thủ công.", 409)
                conn.execute("UPDATE voice_jobs SET state='BLOCKED',error='no committed local WAV after restart',updated_at=? WHERE id=? AND state='RECONCILING'", (now(), job_id))
                self.studio.audit(conn, job["project_id"], "voice.reconciled_no_output", job_id, {"adapter": self.provider.kind})
            return self.job(job_id)
        expected, receipt = self.provider.render(request)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != hashlib.sha256(expected).hexdigest() or inspect_wav(data) != (receipt["sample_rate"], receipt["sample_count"]):
            raise DomainError("WAV không khớp fixture; giữ RECONCILING để kiểm tra thủ công.", 409)
        with self.db.transaction() as conn:
            self._complete(conn, job, request, receipt, relative, None)
        return self.job(job_id)

    def retry(self, job_id: str) -> dict:
        with self.db.transaction() as conn:
            job = dict(require(conn.execute("SELECT * FROM voice_jobs WHERE id=?", (job_id,)).fetchone(), "voice job"))
            if job["state"] != "BLOCKED" or job["provider_request_id"] or conn.execute("SELECT 1 FROM receipts WHERE operation_id=?", (job["operation_id"],)).fetchone():
                raise DomainError("Chỉ thử lại job voice bị chặn trước ghi, chưa có request/receipt.", 409)
            path, _ = self._voice_path(job)
            if path.exists():
                raise DomainError("WAV có thể đã ghi; đối soát trước khi thử lại.", 409)
            request = json.loads(job["request_json"])
            self._prewrite(conn, job, request)
            conn.execute("UPDATE voice_jobs SET state='QUEUED',error=NULL,updated_at=? WHERE id=?", (now(), job_id))
            self.studio.audit(conn, job["project_id"], "voice.retry_requested", job_id, {"operation_id": job["operation_id"]})
        return self.job(job_id)

    def takes(self, shot_id: str) -> list[dict]:
        self.studio.shot(shot_id)
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM voice_takes WHERE shot_id=? ORDER BY performance_id,version DESC", (shot_id,))]

    def take_wav(self, take_id: str) -> Path:
        with self.db.connect() as conn:
            take = require(conn.execute("SELECT * FROM voice_takes WHERE id=?", (take_id,)).fetchone(), "voice take")
        project_root = Path(self.studio.project(take["project_id"])["root"]).resolve()
        path = (project_root / take["relative_path"]).resolve()
        if not path.is_relative_to(project_root) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != take["wav_sha256"]:
            raise DomainError("WAV bị thiếu hoặc thay đổi ngoài app.", 409)
        return path

    def review_take(self, take_id: str, expected_version: int, decision: str) -> dict:
        if decision not in ("APPROVED", "REJECTED"):
            raise DomainError("Quyết định voice phải APPROVED hoặc REJECTED.")
        with self.db.transaction() as conn:
            take = require(conn.execute("SELECT * FROM voice_takes WHERE id=?", (take_id,)).fetchone(), "voice take")
            if take["version"] != expected_version or take["review"] != "NEEDS_REVIEW":
                raise DomainError("Voice take/version đã được duyệt hoặc thay đổi.", 409)
            if take["lock_state"] == "LOCKED":
                raise DomainError("Voice take đã khóa.", 409)
            conn.execute("UPDATE voice_takes SET review=? WHERE id=?", (decision, take_id))
            conn.execute("INSERT INTO approvals(id,project_id,scope,scope_id,exact_version,decision_json,actor,created_at) VALUES(?,?,'voice_take',?,?,?,'local_user',?)",
                         (uid(), take["project_id"], take_id, expected_version, canonical({"decision": decision, "wav_sha256": take["wav_sha256"]}), now()))
            self.studio.audit(conn, take["project_id"], "voice.reviewed", take_id, {"version": expected_version, "decision": decision})
            return dict(require(conn.execute("SELECT * FROM voice_takes WHERE id=?", (take_id,)).fetchone()))

    def preview_lock(self, take_id: str) -> dict:
        with self.db.connect() as conn:
            take = require(conn.execute("SELECT * FROM voice_takes WHERE id=?", (take_id,)).fetchone(), "voice take")
            mapping = self._mapping(conn, take["shot_id"], take["performance_id"])
            if mapping["assets"]["dialogue_id"]["id"] != take["dialogue_version_id"] or mapping["assets"]["voice_profile_id"]["id"] != take["voice_profile_version_id"]:
                raise DomainError("Voice take không khớp dialogue/profile hiện hành.", 409)
            selected = conn.execute("SELECT take_id,revision FROM voice_selections WHERE shot_id=? AND performance_id=?",
                                    (take["shot_id"], take["performance_id"])).fetchone()
            performance = mapping["performance"]
            manifest = mapping["manifest"]
            frames = duration_frames(take["sample_count"], take["sample_rate"], manifest["fps"])
            end = performance["dialogue_start_local_frame"] + frames
            length = manifest["timeline"]["out_exclusive"] - manifest["timeline"]["in"]
            changing = not selected or selected["take_id"] != take_id
            artifacts = [dict(r) for r in conn.execute("SELECT id,role,version FROM artifact_versions WHERE shot_id=? AND validity='CURRENT' AND role IN ('face','audio','render','sound')", (take["shot_id"],))] if changing else []
            return {"take_id": take_id, "take_version": take["version"], "shot_revision": mapping["shot"]["current_revision"],
                    "selected_take_id": selected["take_id"] if selected else None,
                    "selection_revision": selected["revision"] if selected else 0,
                    "allowed": take["review"] == "APPROVED" and take["lock_state"] in ("LOCKED", "UNLOCKED"),
                    "duration_frames": frames, "overflow_frames": max(0, end - length),
                    "affected_subsystems": ["face", "audio", "render", "sound"] if changing else [],
                    "stale_artifacts": artifacts}

    def lock_take(self, take_id: str, expected_version: int, expected_selection_revision: int | None = None) -> dict:
        with self.db.transaction() as conn:
            take = require(conn.execute("SELECT * FROM voice_takes WHERE id=?", (take_id,)).fetchone(), "voice take")
            if take["version"] != expected_version or take["review"] != "APPROVED":
                raise DomainError("Chỉ khóa voice take đã được người dùng duyệt đúng version.", 409)
            approval = conn.execute("SELECT decision_json FROM approvals WHERE project_id=? AND scope='voice_take' AND scope_id=? AND exact_version=?",
                                    (take["project_id"], take_id, expected_version)).fetchone()
            if not approval or json.loads(approval["decision_json"]).get("decision") != "APPROVED":
                raise DomainError("Thiếu quyết định duyệt voice take exact version.", 409)
            mapping = self._mapping(conn, take["shot_id"], take["performance_id"])
            if mapping["assets"]["dialogue_id"]["id"] != take["dialogue_version_id"] or mapping["assets"]["voice_profile_id"]["id"] != take["voice_profile_version_id"]:
                raise DomainError("Voice take không khớp dialogue/profile hiện hành.", 409)
            path = Path(self.studio.project(take["project_id"])["root"]) / take["relative_path"]
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != take["wav_sha256"]:
                raise DomainError("WAV đã mất hoặc thay đổi ngoài app.", 409)
            selected = conn.execute("SELECT * FROM voice_selections WHERE shot_id=? AND performance_id=?", (take["shot_id"], take["performance_id"])).fetchone()
            if expected_selection_revision is not None and (selected["revision"] if selected else 0) != expected_selection_revision:
                raise DomainError("Voice selection đã đổi sau khi xem tác động.", 409)
            if selected and selected["take_id"] == take_id:
                conn.execute("UPDATE voice_takes SET validity='CURRENT' WHERE id=?", (take_id,))
                return dict(require(conn.execute("SELECT * FROM voice_takes WHERE id=?", (take_id,)).fetchone()))
            selection_version = selected["revision"] + 1 if selected else 1
            conn.execute("INSERT INTO voice_selections(shot_id,performance_id,take_id,revision,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(shot_id,performance_id) DO UPDATE SET take_id=excluded.take_id,revision=excluded.revision,updated_at=excluded.updated_at",
                         (take["shot_id"], take["performance_id"], take_id, selection_version, now()))
            conn.execute("UPDATE voice_takes SET lock_state='LOCKED' WHERE id=?", (take_id,))
            conn.execute("UPDATE voice_takes SET validity='CURRENT' WHERE id=?", (take_id,))
            if selected:
                conn.execute("UPDATE voice_takes SET validity='STALE' WHERE id=?", (selected["take_id"],))
            conn.execute("UPDATE timing_revisions SET validity='STALE',review='NEEDS_REVIEW' WHERE shot_id=?", (take["shot_id"],))
            conn.execute("UPDATE video_takes SET validity='STALE' WHERE shot_id=? AND validity='CURRENT'", (take["shot_id"],))
            conn.execute("UPDATE picture_locks SET validity='STALE' WHERE shot_id=? AND validity='CURRENT'", (take["shot_id"],))
            conn.execute("UPDATE sound_stems SET validity='STALE' WHERE shot_id=? AND validity='CURRENT'", (take["shot_id"],))
            conn.execute("UPDATE delivery_packages SET state='STALE' WHERE episode_id=(SELECT episode_id FROM shots WHERE id=?) AND state!='STALE'", (take["shot_id"],))
            for subsystem in ("face", "audio", "render", "sound"):
                conn.execute("UPDATE subsystem_states SET validity='STALE',review='NEEDS_REVIEW',state_revision=state_revision+1 WHERE shot_id=? AND subsystem=?", (take["shot_id"], subsystem))
                conn.execute("UPDATE artifact_versions SET validity='STALE' WHERE shot_id=? AND role=?", (take["shot_id"], subsystem))
            self.studio.audit(conn, take["project_id"], "voice.locked", take_id, {"version": expected_version, "selection_revision": selection_version, "stale": ["face", "audio", "render", "sound"]})
            return dict(require(conn.execute("SELECT * FROM voice_takes WHERE id=?", (take_id,)).fetchone()))

    def _timing_preview(self, conn, shot_id: str) -> dict:
        shot = require(conn.execute("SELECT * FROM shots WHERE id=?", (shot_id,)).fetchone(), "shot")
        manifest = json.loads(conn.execute("SELECT manifest_json FROM shot_revisions WHERE shot_id=? AND revision=?",
                                           (shot_id, shot["current_revision"])).fetchone()[0])
        voiced = [p for p in manifest["performances"] if p.get("voice_profile_id")]
        if not voiced:
            raise DomainError("Shot chưa có performance với voice profile.", 409)
        placements, blockers = [], []
        length = manifest["timeline"]["out_exclusive"] - manifest["timeline"]["in"]
        for performance in voiced:
            pid = performance["performance_id"]
            mapping = self._mapping(conn, shot_id, pid)
            selected = conn.execute("SELECT v.*,s.revision selection_revision FROM voice_selections s JOIN voice_takes v ON v.id=s.take_id WHERE s.shot_id=? AND s.performance_id=?", (shot_id, pid)).fetchone()
            if not selected:
                blockers.append(f"{pid}: chưa khóa voice take")
                continue
            take = dict(selected)
            if take["review"] != "APPROVED" or take["lock_state"] != "LOCKED" or take["validity"] != "CURRENT" or take["dialogue_version_id"] != mapping["assets"]["dialogue_id"]["id"] or take["voice_profile_version_id"] != mapping["assets"]["voice_profile_id"]["id"]:
                blockers.append(f"{pid}: voice take/profile không còn hợp lệ")
                continue
            wav_path = Path(self.studio.project(shot["project_id"])["root"]) / take["relative_path"]
            if not wav_path.is_file() or hashlib.sha256(wav_path.read_bytes()).hexdigest() != take["wav_sha256"]:
                blockers.append(f"{pid}: WAV bị thiếu hoặc đã chỉnh ngoài app")
                continue
            start = performance["dialogue_start_local_frame"]
            frames = duration_frames(take["sample_count"], take["sample_rate"], manifest["fps"])
            end = start + frames
            if end > length:
                blockers.append(f"{pid}: thoại vượt shot {end - length} frame; cần chỉnh shot/timing và duyệt lại")
            placements.append({"performance_id": pid, "character_id": performance["character_id"],
                               "dialogue_id": performance["dialogue_id"], "voice_take_id": take["id"],
                               "voice_version": take["version"], "wav_sha256": take["wav_sha256"],
                               "selection_revision": take["selection_revision"], "start_local_frame": start,
                               "end_local_exclusive": end, "start_episode_frame": manifest["timeline"]["in"] + start,
                               "end_episode_exclusive": manifest["timeline"]["in"] + end})
        return {"shot_id": shot_id, "shot_revision": shot["current_revision"], "fps": manifest["fps"],
                "shot_range": manifest["timeline"], "placements": placements, "blockers": blockers,
                "allowed": not blockers, "input_hash": digest({"shot_revision": shot["current_revision"], "placements": placements})}

    def preview_timing(self, shot_id: str) -> dict:
        with self.db.connect() as conn:
            return self._timing_preview(conn, shot_id)

    def create_timing(self, shot_id: str, expected_shot_revision: int, expected_input_hash: str) -> dict:
        with self.db.transaction() as conn:
            preview = self._timing_preview(conn, shot_id)
            if not preview["allowed"]:
                raise DomainError("; ".join(preview["blockers"]), 409)
            if preview["shot_revision"] != expected_shot_revision or preview["input_hash"] != expected_input_hash:
                raise DomainError("Voice/timing đã thay đổi sau preview.", 409)
            previous = conn.execute("SELECT * FROM timing_revisions WHERE shot_id=? AND input_hash=? AND validity='CURRENT' ORDER BY version DESC LIMIT 1", (shot_id, preview["input_hash"])).fetchone()
            if previous:
                return self._timing(previous)
            version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM timing_revisions WHERE shot_id=?", (shot_id,)).fetchone()[0]
            item = {"id": uid(), "shot_id": shot_id, "version": version, "shot_revision": expected_shot_revision,
                    "input_hash": preview["input_hash"], "placements_json": canonical(preview["placements"]),
                    "review": "NEEDS_REVIEW", "validity": "CURRENT", "created_at": now()}
            conn.execute("INSERT INTO timing_revisions(id,shot_id,version,shot_revision,input_hash,placements_json,review,validity,created_at) VALUES(:id,:shot_id,:version,:shot_revision,:input_hash,:placements_json,:review,:validity,:created_at)", item)
            project_id = conn.execute("SELECT project_id FROM shots WHERE id=?", (shot_id,)).fetchone()[0]
            self.studio.audit(conn, project_id, "timing.created", item["id"], {"version": version, "input_hash": item["input_hash"]})
            return self._timing(item)

    def _timing(self, row) -> dict:
        result = dict(row)
        result["placements"] = json.loads(result.pop("placements_json"))
        return result

    def timings(self, shot_id: str) -> list[dict]:
        self.studio.shot(shot_id)
        with self.db.connect() as conn:
            return [self._timing(r) for r in conn.execute("SELECT * FROM timing_revisions WHERE shot_id=? ORDER BY version DESC", (shot_id,))]

    def approve_timing(self, timing_id: str, expected_version: int) -> dict:
        with self.db.transaction() as conn:
            row = require(conn.execute("SELECT * FROM timing_revisions WHERE id=?", (timing_id,)).fetchone(), "timing revision")
            preview = self._timing_preview(conn, row["shot_id"])
            if row["version"] != expected_version or row["input_hash"] != preview["input_hash"] or not preview["allowed"] or row["validity"] != "CURRENT" or row["review"] != "NEEDS_REVIEW":
                raise DomainError("Timing không còn khớp voice/shot hiện hành.", 409)
            conn.execute("UPDATE timing_revisions SET review='APPROVED' WHERE id=?", (timing_id,))
            project_id = conn.execute("SELECT project_id FROM shots WHERE id=?", (row["shot_id"],)).fetchone()[0]
            conn.execute("INSERT INTO approvals(id,project_id,scope,scope_id,exact_version,decision_json,actor,created_at) VALUES(?,?, 'timing',?,?,?,'local_user',?)",
                         (uid(), project_id, row["shot_id"], expected_version, canonical({"input_hash": row["input_hash"]}), now()))
            self.studio.audit(conn, project_id, "timing.approved", timing_id, {"version": expected_version})
            return self._timing(require(conn.execute("SELECT * FROM timing_revisions WHERE id=?", (timing_id,)).fetchone()))

    def facial_plan(self, shot_id: str) -> dict:
        shot = self.studio.shot(shot_id)
        timing = self.timings(shot_id)
        if not timing or timing[0]["review"] != "APPROVED" or timing[0]["validity"] != "CURRENT" or timing[0]["shot_revision"] != shot["current_revision"]:
            raise DomainError("Cần timing revision hiện hành đã được duyệt.", 409)
        with self.db.connect() as conn:
            for subsystem in ("face", "audio"):
                state = require(conn.execute("SELECT * FROM subsystem_states WHERE shot_id=? AND subsystem=?", (shot_id, subsystem)).fetchone())
                if state["mode"] != "AUTO" or state["lock_state"] != "UNLOCKED" or (state["observed_fingerprint"] and state["observed_fingerprint"] != state["synced_fingerprint"]):
                    raise DomainError(f"{subsystem}: manual/locked hoặc chỉnh ngoài app.", 409)
            refs = {}
            wav_paths = {}
            for placement in timing[0]["placements"]:
                take = require(conn.execute("SELECT * FROM voice_takes WHERE id=?", (placement["voice_take_id"],)).fetchone(), "voice take")
                if take["version"] != placement["voice_version"] or take["wav_sha256"] != placement["wav_sha256"] or take["lock_state"] != "LOCKED":
                    raise DomainError("Face plan cần đúng voice take/version đã khóa.", 409)
                wav_paths[placement["performance_id"]] = take["relative_path"]
                pinned = shot["resolved_registry"][placement["character_id"]]
                asset = require(conn.execute("SELECT metadata_json FROM asset_versions WHERE id=?", (pinned["asset_version_id"],)).fetchone())
                meta = json.loads(asset["metadata_json"])
                if not meta.get("metahuman_actor_path"):
                    raise DomainError("MetaHuman actor path chưa được người dùng đăng ký.", 409)
                refs[placement["performance_id"]] = actor_path(meta["metahuman_actor_path"], "character.metahuman_actor_path")
        return {"schema_version": "p3.facial-plan.1", "shot_id": shot_id, "shot_revision": shot["current_revision"],
                "timing_version": timing[0]["version"], "timing_input_hash": timing[0]["input_hash"],
                "pipeline_stages": ["resolve_registered_metahuman", "import_locked_wav", "solve", "bake_export", "bind_face_audio_tracks"],
                "placements": [dict(p, metahuman_actor_path=refs[p["performance_id"]], wav_relative_path=wav_paths[p["performance_id"]]) for p in timing[0]["placements"]],
                "capability": "BLOCKED_P0_FACIAL_UNVERIFIED"}

    def facial_preflight(self, shot_id: str) -> dict:
        plan = self.facial_plan(shot_id)
        FacialAdapter().preflight(plan)
        return plan

    def camera_plan(self, shot_id: str) -> dict:
        shot = self.studio.shot(shot_id)
        state = next(s for s in shot["states"] if s["subsystem"] == "camera")
        if state["mode"] != "AUTO" or state["lock_state"] != "UNLOCKED" or (state["observed_fingerprint"] and state["observed_fingerprint"] != state["synced_fingerprint"]):
            raise DomainError("Camera manual/locked hoặc chỉnh ngoài app.", 409)
        plan = resolve_camera_intent(shot["manifest"])
        if plan["mode"] == "follow_walking":
            subject = plan["subjects"][0]
            performance = next(p for p in shot["manifest"]["performances"] if p["character_id"] == subject)
            pinned = shot["resolved_registry"][performance["animation_id"]]
            with self.db.connect() as conn:
                asset = require(conn.execute("SELECT metadata_json FROM asset_versions WHERE id=?", (pinned["asset_version_id"],)).fetchone())
            if "walk" not in json.loads(asset["metadata_json"]).get("semantic_tags", []):
                raise DomainError("Follow walking cần animation có nhãn walk trong registry.", 409)
        return {**plan, "shot_id": shot_id, "shot_revision": shot["current_revision"]}

    def camera_preflight(self, shot_id: str) -> dict:
        plan = self.camera_plan(shot_id)
        CameraAdapter().preflight(plan)
        return plan
