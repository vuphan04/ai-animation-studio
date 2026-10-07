"""Picture-pinned Foley/stem bookkeeping and manual Resolve handoff."""

from __future__ import annotations

import json
import shutil
import wave
import re
from pathlib import Path
from fractions import Fraction
from xml.etree import ElementTree as ET

from .domain import DomainError, canonical, digest
from .foley_adapter import DisabledFoleyWorker, FoleyWorker
from .p4_domain import file_sha256
from .service import Studio, now, require, uid
from .p6_video_service import VideoService

STEM_ROLES = ("Dialogue", "Foley", "SFX", "Ambience", "Music")


class SoundDeliveryService:
    def __init__(self, studio: Studio, video: VideoService, cache_render, worker: FoleyWorker | None = None):
        self.studio, self.video, self.cache_render = studio, video, cache_render
        self.worker = worker or DisabledFoleyWorker()
        self.active_operations: set[str] = set()
        with studio.db.transaction() as conn:
            conn.execute("UPDATE foley_jobs SET state='RECONCILING',error='App khởi động lại; kiểm tra output và GPU lease.' WHERE state='RUNNING'")
            conn.execute("UPDATE foley_attempts SET state='RECONCILING',ended_at=?,error='Backend restarted during Foley attempt' WHERE state='RUNNING'", (now(),))
            conn.execute("UPDATE delivery_packages SET state='RECONCILING' WHERE state='BUILDING'")

    def _picture(self, conn, shot_id: str):
        shot = require(conn.execute("SELECT * FROM shots WHERE id=?", (shot_id,)).fetchone(), "shot")
        lock = require(conn.execute("SELECT * FROM picture_locks WHERE shot_id=? AND validity='CURRENT' ORDER BY version DESC LIMIT 1", (shot_id,)).fetchone(), "Picture Lock")
        selected = conn.execute("SELECT take_id FROM video_selections WHERE shot_id=? AND stage='FINAL'", (shot_id,)).fetchone()
        if lock["shot_revision"] != shot["current_revision"] or not selected or selected[0] != lock["final_take_id"]:
            raise DomainError("Picture Lock không còn khớp shot/selected final take.", 409)
        approval = conn.execute("SELECT 1 FROM approvals WHERE project_id=? AND scope='picture_lock' AND scope_id=? AND exact_version=?",
                                (shot["project_id"], shot_id, lock["version"])).fetchone()
        if not approval:
            raise DomainError("Thiếu C6 approval exact Picture Lock version.", 409)
        if lock["timing_version"] is not None:
            timing = conn.execute("SELECT * FROM timing_revisions WHERE shot_id=? AND version=?", (shot_id, lock["timing_version"])).fetchone()
            if not timing or timing["review"] != "APPROVED" or timing["validity"] != "CURRENT" or not self.video._timing_current(conn, timing):
                raise DomainError("Dialogue canon/timing không còn đúng Picture Lock.", 409)
        take = require(conn.execute("SELECT * FROM video_takes WHERE id=?", (lock["final_take_id"],)).fetchone(), "final take")
        if take["review"] != "APPROVED" or take["validity"] != "CURRENT" or take["shot_revision"] != shot["current_revision"]:
            raise DomainError("Selected final take đã mất review/current.", 409)
        self.video._checked_file(shot["project_id"], take["relative_path"], take["sha256"])
        return shot, lock, take

    def _sound_state(self, conn, shot_id: str):
        state = require(conn.execute("SELECT * FROM subsystem_states WHERE shot_id=? AND subsystem='sound'", (shot_id,)).fetchone(), "sound state")
        if state["mode"] != "AUTO" or state["lock_state"] != "UNLOCKED" or (state["observed_fingerprint"] and state["observed_fingerprint"] != state["synced_fingerprint"]):
            raise DomainError("Sound MANUAL/LOCKED hoặc có chỉnh ngoài app; Foley không được ghi.", 409)
        return state

    def foley_preflight(self, shot_id: str) -> dict:
        blockers = []
        try:
            with self.studio.db.connect() as conn:
                shot, lock, take = self._picture(conn, shot_id)
                self._sound_state(conn, shot_id)
        except DomainError as exc:
            blockers.append(str(exc))
            lock = take = None
        if not self.worker.operational:
            blockers.append("Foley worker chưa chọn model/runtime và benchmark P0")
        lease = self.cache_render.heavy_lease()
        if lease:
            blockers.append(f"GPU_HEAVY đang do {lease['owner_kind']} giữ; Foley không chạy song song MRQ")
        return {"allowed": not blockers, "blockers": blockers, "picture_lock_id": lock["id"] if lock else None,
                "source_take_id": take["id"] if take else None, "worker_kind": self.worker.kind, "lease": lease}

    def enqueue_foley(self, shot_id: str, client_request_id: str) -> dict:
        check = self.foley_preflight(shot_id)
        if not check["allowed"]:
            raise DomainError("; ".join(check["blockers"]), 409)
        if not isinstance(client_request_id, str) or not client_request_id.strip() or len(client_request_id) > 128:
            raise DomainError("Client request ID không hợp lệ.")
        with self.studio.db.transaction() as conn:
            shot, lock, take = self._picture(conn, shot_id)
            sound_state = self._sound_state(conn, shot_id)
            input_hash = digest({"picture_lock_id": lock["id"], "picture_hash": lock["input_hash"], "take_sha256": take["sha256"], "worker": self.worker.kind, "sound_state_revision": sound_state["state_revision"]})
            operation_id = digest({"input_hash": input_hash, "client_request_id": client_request_id})
            existing = conn.execute("SELECT * FROM foley_jobs WHERE operation_id=?", (operation_id,)).fetchone()
            if existing:
                return dict(existing)
            pending = conn.execute("SELECT * FROM foley_jobs WHERE shot_id=? AND input_hash=? AND state IN ('QUEUED','RUNNING','RECONCILING')", (shot_id, input_hash)).fetchone()
            if pending:
                return dict(pending)
            item = {"id": uid(), "project_id": shot["project_id"], "shot_id": shot_id,
                    "picture_lock_id": lock["id"], "operation_id": operation_id, "input_hash": input_hash,
                    "source_take_id": take["id"], "state": "QUEUED", "attempt": 0, "error": None,
                    "created_at": now(), "updated_at": now()}
            conn.execute("INSERT INTO foley_jobs VALUES(:id,:project_id,:shot_id,:picture_lock_id,:operation_id,:input_hash,:source_take_id,:state,:attempt,:error,:created_at,:updated_at)", item)
            self.studio.audit(conn, shot["project_id"], "foley.queued", item["id"], {"picture_lock_id": lock["id"], "input_hash": input_hash})
            return item

    def foley_jobs(self, project_id: str) -> list[dict]:
        self.studio.project(project_id)
        with self.studio.db.connect() as conn:
            return [dict(row, attempts=[dict(a) for a in conn.execute("SELECT * FROM foley_attempts WHERE job_id=? ORDER BY attempt", (row["id"],))]) for row in conn.execute("SELECT * FROM foley_jobs WHERE project_id=? ORDER BY created_at DESC", (project_id,))]

    def process_next(self) -> dict | None:
        if self.cache_render.heavy_lease():
            return None
        with self.studio.db.transaction() as conn:
            row = conn.execute("SELECT * FROM foley_jobs WHERE state='QUEUED' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            job = dict(row)
            try:
                shot, lock, take = self._picture(conn, job["shot_id"])
                state = self._sound_state(conn, job["shot_id"])
                if lock["id"] != job["picture_lock_id"] or take["id"] != job["source_take_id"]:
                    raise DomainError("Picture Lock đổi trước Foley.", 409)
                if digest({"picture_lock_id": lock["id"], "picture_hash": lock["input_hash"], "take_sha256": take["sha256"], "worker": self.worker.kind, "sound_state_revision": state["state_revision"]}) != job["input_hash"] or not self.worker.operational:
                    raise DomainError("Worker hoặc sound revision đổi trước Foley.", 409)
            except DomainError as exc:
                conn.execute("UPDATE foley_jobs SET state='BLOCKED',error=?,updated_at=? WHERE id=?", (str(exc), now(), job["id"]))
                return dict(conn.execute("SELECT * FROM foley_jobs WHERE id=?", (job["id"],)).fetchone())
            conn.execute("UPDATE foley_jobs SET state='RUNNING',attempt=attempt+1,updated_at=? WHERE id=?", (now(), job["id"]))
            conn.execute("INSERT INTO foley_attempts(id,job_id,attempt,state,started_at) VALUES(?,?,?,'RUNNING',?)", (uid(), job["id"], job["attempt"] + 1, now()))
        try:
            self.cache_render.acquire_heavy_lease(job["operation_id"], "FOLEY")
        except DomainError:
            with self.studio.db.transaction() as conn:
                conn.execute("UPDATE foley_jobs SET state='QUEUED',updated_at=? WHERE id=?", (now(), job["id"]))
                conn.execute("UPDATE foley_attempts SET state='DEFERRED',ended_at=? WHERE job_id=? AND attempt=?", (now(), job["id"], job["attempt"] + 1))
            return None
        request = {"operation_id": job["operation_id"], "picture_lock_id": job["picture_lock_id"],
                   "picture_hash": lock["input_hash"], "source_take_id": take["id"],
                   "source_relative_path": take["relative_path"], "source_sha256": take["sha256"],
                   "dialogue_audio": "FORBIDDEN", "duration_frames": lock["edit_out_exclusive"] - lock["edit_in"]}
        def before_write():
            with self.studio.db.connect() as conn:
                _, observed_lock, observed_take = self._picture(conn, job["shot_id"])
                observed_state = self._sound_state(conn, job["shot_id"])
                if observed_lock["id"] != lock["id"] or observed_take["sha256"] != take["sha256"] or observed_state["state_revision"] != state["state_revision"]:
                    raise DomainError("Picture/sound lock hoặc revision đổi trước Foley write.", 409)
            self.cache_render.heartbeat_heavy_lease(job["operation_id"])
        self.active_operations.add(job["operation_id"])
        try:
            before_write()
            output = self.worker.generate(request, before_write=before_write)
            # Registration independently verifies the resulting WAV and exact lock.
            relative, _ = self.video._checked_file(job["project_id"], output.relative_path, output.sha256)
            stem = self.register_stem(job["shot_id"], job["picture_lock_id"], "Foley", relative,
                                      output.sha256, offset_frame=0, provenance=output.provenance)
            with self.studio.db.transaction() as conn:
                receipt = {"schema_version": "p6.foley-result.1", "operation_id": job["operation_id"],
                           "input_hash": job["input_hash"], "picture_lock_id": lock["id"], "source_sha256": take["sha256"],
                           "stem_id": stem["id"], "relative_path": stem["relative_path"], "sha256": stem["sha256"],
                           "provenance": output.provenance}
                conn.execute("INSERT INTO receipts(id,operation_id,input_hash,status,output_json,created_at) VALUES(?,?,?,'COMPLETED',?,?)", (uid(), job["operation_id"], job["input_hash"], canonical(receipt), now()))
                conn.execute("UPDATE foley_jobs SET state='COMPLETED',updated_at=? WHERE id=?", (now(), job["id"]))
                conn.execute("UPDATE foley_attempts SET state='COMPLETED',ended_at=? WHERE job_id=? AND attempt=?", (now(), job["id"], job["attempt"] + 1))
                conn.execute("DELETE FROM gpu_heavy_leases WHERE operation_id=? AND owner_kind='FOLEY' AND state='ACTIVE'", (job["operation_id"],))
                self.studio.audit(conn, job["project_id"], "foley.completed", job["id"], {"stem_id": stem["id"], "sha256": output.sha256})
        except Exception:
            with self.studio.db.transaction() as conn:
                conn.execute("UPDATE foley_jobs SET state='RECONCILING',error='Foley output chưa rõ; cần kiểm file và lease trước retry.',updated_at=? WHERE id=?", (now(), job["id"]))
                conn.execute("UPDATE foley_attempts SET state='RECONCILING',ended_at=?,error='Output outcome uncertain; no automatic retry' WHERE job_id=? AND attempt=?", (now(), job["id"], job["attempt"] + 1))
                conn.execute("UPDATE gpu_heavy_leases SET state='RECONCILING' WHERE operation_id=?", (job["operation_id"],))
        finally:
            self.active_operations.discard(job["operation_id"])
        with self.studio.db.connect() as conn:
            return dict(conn.execute("SELECT * FROM foley_jobs WHERE id=?", (job["id"],)).fetchone())

    def register_stem(self, shot_id: str, picture_lock_id: str, role: str, relative_path: str,
                      sha256: str, offset_frame: int, provenance: str = "MANUAL_WAV_REGISTRATION") -> dict:
        if role not in STEM_ROLES or type(offset_frame) is not int or offset_frame < 0:
            raise DomainError("Stem role/offset frame không hợp lệ.")
        shot = self.studio.shot(shot_id)
        relative, path = self.video._checked_file(shot["project_id"], relative_path, sha256)
        try:
            with wave.open(str(path), "rb") as wav:
                if wav.getnchannels() not in (1, 2) or wav.getsampwidth() not in (2, 3, 4):
                    raise DomainError("Stem WAV cần PCM mono/stereo 16/24/32-bit.")
                rate, count = wav.getframerate(), wav.getnframes()
        except (wave.Error, EOFError) as exc:
            raise DomainError("Không đọc được metadata WAV PCM của stem.") from exc
        with self.studio.db.transaction() as conn:
            current, lock, take = self._picture(conn, shot_id)
            if lock["id"] != picture_lock_id:
                raise DomainError("Stem phải pin đúng Picture Lock current.", 409)
            self._sound_state(conn, shot_id)
            if offset_frame >= lock["edit_out_exclusive"] - lock["edit_in"]:
                raise DomainError("Offset stem vượt picture range.")
            manifest = json.loads(conn.execute("SELECT manifest_json FROM shot_revisions WHERE shot_id=? AND revision=?", (shot_id, current["current_revision"])).fetchone()[0])
            fps = manifest["fps"]
            # PCM rounding may exceed the rational endpoint by at most one sample.
            remaining = lock["edit_out_exclusive"] - lock["edit_in"] - offset_frame
            if count <= 0 or rate <= 0 or count * fps["num"] > remaining * rate * fps["den"] + fps["num"]:
                raise DomainError("Stem vượt Picture Lock hoặc không có sample hợp lệ.", 409)
            if role == "Dialogue":
                selections = conn.execute("SELECT v.* FROM voice_selections s JOIN voice_takes v ON v.id=s.take_id WHERE s.shot_id=?", (shot_id,)).fetchall()
                silent = provenance == "LOCAL_SILENCE_NO_DIALOGUE" and not any(p.get("dialogue_id") for p in manifest["performances"]) and not selections and offset_frame == 0
                if silent:
                    with wave.open(str(path), "rb") as wav:
                        while chunk := wav.readframes(65536):
                            if any(chunk):
                                raise DomainError("Dialogue không có thoại chỉ nhận stem im lặng do app tạo.", 409)
                elif len(selections) != 1 or selections[0]["lock_state"] != "LOCKED" or selections[0]["review"] != "APPROVED" or selections[0]["validity"] != "CURRENT" or selections[0]["wav_sha256"] != sha256.lower() or selections[0]["relative_path"] != relative:
                    raise DomainError("Dialogue stem phải là đúng một canonical locked voice WAV; mix nhiều giọng cần receipt riêng.", 409)
                if not silent:
                    timing = conn.execute("SELECT * FROM timing_revisions WHERE shot_id=? AND version=? AND validity='CURRENT' AND review='APPROVED'", (shot_id, lock["timing_version"])).fetchone()
                    placements = json.loads(timing["placements_json"]) if timing else []
                    if len(placements) != 1 or placements[0]["voice_take_id"] != selections[0]["id"] or placements[0]["start_local_frame"] != offset_frame:
                        raise DomainError("Dialogue offset phải khớp exact C2 timing đã pin trong Picture Lock.", 409)
                    provenance = "CANONICAL_LOCKED_VOICE"
            elif provenance not in ("MANUAL_WAV_REGISTRATION", "FAKE_FOLEY_TEST", "FOLEY_WORKER_VERIFIED"):
                raise DomainError("Stem provenance không hợp lệ.")
            old = conn.execute("SELECT * FROM sound_stems WHERE shot_id=? AND role=? AND picture_lock_id=? AND sha256=? AND offset_frame=? AND validity='CURRENT'",
                               (shot_id, role, lock["id"], sha256.lower(), offset_frame)).fetchone()
            if old:
                return dict(old)
            version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM sound_stems WHERE shot_id=? AND role=?", (shot_id, role)).fetchone()[0]
            conn.execute("UPDATE sound_stems SET validity='STALE' WHERE shot_id=? AND role=? AND validity='CURRENT'", (shot_id, role))
            item = {"id": uid(), "project_id": shot["project_id"], "shot_id": shot_id, "picture_lock_id": lock["id"],
                    "role": role, "version": version, "relative_path": relative, "sha256": sha256.lower(),
                    "sample_rate": rate, "sample_count": count, "offset_frame": offset_frame,
                    "review": "NEEDS_REVIEW", "validity": "CURRENT", "provenance": provenance, "created_at": now()}
            conn.execute("INSERT INTO sound_stems VALUES(:id,:project_id,:shot_id,:picture_lock_id,:role,:version,:relative_path,:sha256,:sample_rate,:sample_count,:offset_frame,:review,:validity,:provenance,:created_at)", item)
            conn.execute("UPDATE delivery_packages SET state='STALE' WHERE episode_id=? AND state!='STALE'", (shot["episode_id"],))
            self.studio.audit(conn, shot["project_id"], "sound.stem_registered", item["id"], {"role": role, "version": version, "picture_lock_id": lock["id"]})
            return item

    def create_silent_dialogue(self, shot_id: str, expected_picture_lock_id: str) -> dict:
        shot = self.studio.shot(shot_id)
        if any(p.get("dialogue_id") for p in shot["manifest"]["performances"]):
            raise DomainError("Cảnh có dialogue canon; không được tạo Dialogue im lặng thay giọng.", 409)
        with self.studio.db.connect() as conn:
            _, lock, _ = self._picture(conn, shot_id)
            if lock["id"] != expected_picture_lock_id:
                raise DomainError("Picture Lock đổi trước tạo Dialogue im lặng.", 409)
            self._sound_state(conn, shot_id)
        fps = shot["manifest"]["fps"]
        sample_count = round(Fraction((lock["edit_out_exclusive"] - lock["edit_in"]) * 48000 * fps["den"], fps["num"]))
        relative = f"audio/dialogue/silence-{lock['id']}.wav"
        _, path = self.video._path(shot["project_id"], relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            with path.open("xb") as raw:
                with wave.open(raw, "wb") as wav:
                    wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(48000)
                    for start in range(0, sample_count, 65536):
                        wav.writeframes(b"\0\0" * min(65536, sample_count - start))
        return self.register_stem(shot_id, lock["id"], "Dialogue", relative, file_sha256(path), 0, "LOCAL_SILENCE_NO_DIALOGUE")

    def reconcile_foley(self, job_id: str) -> dict:
        with self.studio.db.transaction() as conn:
            job = require(conn.execute("SELECT * FROM foley_jobs WHERE id=?", (job_id,)).fetchone(), "Foley job")
            if job["state"] == "COMPLETED":
                return dict(job)
            if job["state"] != "RECONCILING" or job["operation_id"] in self.active_operations:
                raise DomainError("Foley worker chưa dừng hoặc job không chờ đối soát.", 409)
            receipt = conn.execute("SELECT * FROM receipts WHERE operation_id=? AND input_hash=? AND status='COMPLETED'", (job["operation_id"], job["input_hash"])).fetchone()
            if not receipt:
                raise DomainError("Chưa có receipt Foley durable; giữ job/lease RECONCILING và kiểm output thủ công.", 409)
            output = json.loads(receipt["output_json"])
            self.video._checked_file(job["project_id"], output["relative_path"], output["sha256"])
            stem = require(conn.execute("SELECT * FROM sound_stems WHERE id=? AND picture_lock_id=?", (output["stem_id"], job["picture_lock_id"])).fetchone(), "Foley output stem")
            if stem["sha256"] != output["sha256"]:
                raise DomainError("Foley receipt không khớp stem.", 409)
            conn.execute("UPDATE foley_jobs SET state='COMPLETED',error=NULL,updated_at=? WHERE id=?", (now(), job_id))
            conn.execute("DELETE FROM gpu_heavy_leases WHERE operation_id=? AND owner_kind='FOLEY' AND state='RECONCILING'", (job["operation_id"],))
            return dict(conn.execute("SELECT * FROM foley_jobs WHERE id=?", (job_id,)).fetchone())

    def stems(self, shot_id: str) -> list[dict]:
        self.studio.shot(shot_id)
        with self.studio.db.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM sound_stems WHERE shot_id=? ORDER BY role,version DESC", (shot_id,))]

    def review_stem(self, stem_id: str, expected_version: int) -> dict:
        with self.studio.db.transaction() as conn:
            stem = require(conn.execute("SELECT * FROM sound_stems WHERE id=?", (stem_id,)).fetchone(), "stem")
            _, lock, _ = self._picture(conn, stem["shot_id"])
            if stem["version"] != expected_version or stem["review"] != "NEEDS_REVIEW" or stem["validity"] != "CURRENT" or lock["id"] != stem["picture_lock_id"]:
                raise DomainError("Stem version hoặc Picture Lock đã đổi.", 409)
            self.video._checked_file(stem["project_id"], stem["relative_path"], stem["sha256"])
            conn.execute("UPDATE sound_stems SET review='APPROVED' WHERE id=?", (stem_id,))
            conn.execute("INSERT INTO approvals(id,project_id,scope,scope_id,exact_version,decision_json,actor,created_at) VALUES(?,?,'sound_stem',?,?,?,'local_user',?)",
                         (uid(), stem["project_id"], stem_id, expected_version, canonical({"picture_lock_id": lock["id"], "sha256": stem["sha256"]}), now()))
            return dict(conn.execute("SELECT * FROM sound_stems WHERE id=?", (stem_id,)).fetchone())

    def delivery_profile(self, episode_id: str) -> dict:
        with self.studio.db.connect() as conn:
            require(conn.execute("SELECT id FROM episodes WHERE id=?", (episode_id,)).fetchone(), "episode")
            row = conn.execute("SELECT * FROM delivery_profiles WHERE episode_id=?", (episode_id,)).fetchone()
            return dict(row) if row else {"episode_id": episode_id, "revision": 0, "width": 1280, "height": 720,
                                        "color_profile": "", "audio_sample_rate": 48000, "timecode_start": "01:00:00:00"}

    def save_delivery_profile(self, episode_id: str, expected_revision: int, width: int, height: int,
                              color_profile: str, audio_sample_rate: int, timecode_start: str) -> dict:
        if type(width) is not int or type(height) is not int or not 16 <= width <= 8192 or not 16 <= height <= 8192 or not isinstance(color_profile, str) or not color_profile.strip() or len(color_profile) > 128 or type(audio_sample_rate) is not int or audio_sample_rate not in (44100, 48000, 96000) or not isinstance(timecode_start, str) or not re.fullmatch(r"\d{2}:[0-5]\d:[0-5]\d:\d{2}", timecode_start):
            raise DomainError("Delivery profile cần resolution, color, sample rate và timecode HH:MM:SS:FF hợp lệ.")
        with self.studio.db.transaction() as conn:
            episode = require(conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone(), "episode")
            old = conn.execute("SELECT revision FROM delivery_profiles WHERE episode_id=?", (episode_id,)).fetchone()
            if (old[0] if old else 0) != expected_revision:
                raise DomainError("Delivery profile revision conflict; tải lại cấu hình.", 409)
            item = {"episode_id": episode_id, "revision": expected_revision + 1, "width": width, "height": height,
                    "color_profile": color_profile.strip(), "audio_sample_rate": audio_sample_rate,
                    "timecode_start": timecode_start, "updated_at": now()}
            conn.execute("INSERT INTO delivery_profiles VALUES(:episode_id,:revision,:width,:height,:color_profile,:audio_sample_rate,:timecode_start,:updated_at) ON CONFLICT(episode_id) DO UPDATE SET revision=excluded.revision,width=excluded.width,height=excluded.height,color_profile=excluded.color_profile,audio_sample_rate=excluded.audio_sample_rate,timecode_start=excluded.timecode_start,updated_at=excluded.updated_at", item)
            conn.execute("INSERT INTO approvals(id,project_id,scope,scope_id,exact_version,decision_json,actor,created_at) VALUES(?,?,'delivery_profile',?,?,?,'local_user',?)", (uid(), episode["project_id"], episode_id, item["revision"], canonical(item), now()))
            conn.execute("UPDATE delivery_packages SET state='STALE' WHERE episode_id=? AND state!='STALE'", (episode_id,))
            self.studio.audit(conn, episode["project_id"], "delivery.profile_approved", episode_id, {"revision": item["revision"]})
            return item

    def delivery_preflight(self, episode_id: str) -> dict:
        with self.studio.db.connect() as conn:
            episode = require(conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone(), "episode")
            shots = conn.execute("SELECT * FROM shots WHERE episode_id=? ORDER BY ordinal", (episode_id,)).fetchall()
            blockers = []
            items = []
            profile_row = conn.execute("SELECT * FROM delivery_profiles WHERE episode_id=?", (episode_id,)).fetchone()
            profile = dict(profile_row) if profile_row else None
            if not profile or not conn.execute("SELECT 1 FROM approvals WHERE scope='delivery_profile' AND scope_id=? AND exact_version=?", (episode_id, profile["revision"])).fetchone():
                blockers.append("Cần lưu và duyệt delivery profile exact version trong app.")
            if not shots:
                blockers.append("Tập chưa có shot.")
            fps = None
            for shot in shots:
                try:
                    _, lock, take = self._picture(conn, shot["id"])
                except DomainError as exc:
                    blockers.append(f"{shot['code']}: {exc}")
                    continue
                manifest = json.loads(conn.execute("SELECT manifest_json FROM shot_revisions WHERE shot_id=? AND revision=?", (shot["id"], shot["current_revision"])).fetchone()[0])
                if fps is None:
                    fps = manifest["fps"]
                elif fps != manifest["fps"]:
                    blockers.append("Các shot trong một delivery cần cùng FPS rational.")
                stems = []
                for role in STEM_ROLES:
                    row = conn.execute("SELECT * FROM sound_stems WHERE shot_id=? AND role=? AND validity='CURRENT' ORDER BY version DESC LIMIT 1", (shot["id"], role)).fetchone()
                    if not row or row["picture_lock_id"] != lock["id"] or row["review"] != "APPROVED":
                        blockers.append(f"{shot['code']}: thiếu {role} stem current/approved đúng Picture Lock")
                        continue
                    if not self.video._file_current(episode["project_id"], row):
                        blockers.append(f"{shot['code']}: {role} stem thiếu/đổi checksum")
                    if profile and row["sample_rate"] != profile["audio_sample_rate"]:
                        blockers.append(f"{shot['code']}: {role} sample rate khác profile; cần chuyển đổi thủ công thành version mới")
                    if not conn.execute("SELECT 1 FROM approvals WHERE scope='sound_stem' AND scope_id=? AND exact_version=?", (row["id"], row["version"])).fetchone():
                        blockers.append(f"{shot['code']}: {role} thiếu approval exact version")
                    stems.append(dict(row))
                items.append({"shot_id": shot["id"], "shot_code": shot["code"], "picture_lock": dict(lock),
                              "take": dict(take), "fps": manifest["fps"], "timeline": manifest["timeline"], "stems": stems})
            if fps and fps["den"] != 1:
                blockers.append("FCP7 XML thử nghiệm chỉ xuất FPS nguyên; cần chọn interchange profile khác và duyệt.")
            if fps and profile and int(profile["timecode_start"].split(":")[-1]) >= fps["num"] // fps["den"]:
                blockers.append("Timecode FF vượt timebase của delivery.")
            for before, after in zip(items, items[1:]):
                if before["timeline"]["out_exclusive"] != after["timeline"]["in"]:
                    blockers.append("Timeline bàn giao có gap/overlap; cần duyệt edit revision rõ trước export.")
            return {"allowed": not blockers, "blockers": blockers, "project_id": episode["project_id"],
                    "episode_id": episode_id, "fps": fps, "shots": items, "profile": profile,
                    "format": "FCP7_XML_MANUAL_IMPORT_UNVERIFIED_RESOLVE"}

    @staticmethod
    def _xml(items: list[dict], fps: int, package_root: Path, profile: dict) -> bytes:
        xmeml = ET.Element("xmeml", version="5")
        sequence = ET.SubElement(xmeml, "sequence")
        ET.SubElement(sequence, "name").text = "AI Animation Studio delivery"
        ET.SubElement(sequence, "duration").text = str(max(item["timeline"]["out_exclusive"] for item in items))
        rate = ET.SubElement(sequence, "rate")
        ET.SubElement(rate, "timebase").text = str(fps)
        ET.SubElement(rate, "ntsc").text = "FALSE"
        tc = ET.SubElement(sequence, "timecode")
        ET.SubElement(tc, "string").text = profile["timecode_start"]
        h, m, s, f = map(int, profile["timecode_start"].split(":"))
        ET.SubElement(tc, "frame").text = str(((h * 60 + m) * 60 + s) * fps + f)
        ET.SubElement(tc, "displayformat").text = "NDF"
        tc.append(ET.fromstring(ET.tostring(rate)))
        media = ET.SubElement(sequence, "media")
        video = ET.SubElement(media, "video")
        sample = ET.SubElement(ET.SubElement(video, "format"), "samplecharacteristics")
        ET.SubElement(sample, "width").text = str(profile["width"])
        ET.SubElement(sample, "height").text = str(profile["height"])
        sample.append(ET.fromstring(ET.tostring(rate)))
        video_track = ET.SubElement(video, "track")
        audio = ET.SubElement(media, "audio")
        audio_tracks = {role: ET.SubElement(audio, "track") for role in STEM_ROLES}
        for item in items:
            start, end = item["timeline"]["in"], item["timeline"]["out_exclusive"]
            for role, record, track, offset in [("Picture", item["take"], video_track, 0),
                                                *((role, stem, audio_tracks[role], stem["offset_frame"]) for role in STEM_ROLES for stem in item["stems"] if stem["role"] == role)]:
                clip = ET.SubElement(track, "clipitem", id=f"{item['shot_code']}-{role}")
                ET.SubElement(clip, "name").text = f"{item['shot_code']} {role}"
                ET.SubElement(clip, "start").text = str(start + offset)
                length = end - start - offset if role == "Picture" else min(end - start - offset, -(-record["sample_count"] * fps // record["sample_rate"]))
                ET.SubElement(clip, "end").text = str(start + offset + length)
                ET.SubElement(clip, "in").text = "0"
                ET.SubElement(clip, "out").text = str(length)
                clip.append(ET.fromstring(ET.tostring(rate)))
                file = ET.SubElement(clip, "file", id=record["id"])
                ET.SubElement(file, "name").text = Path(record["relative_path"]).name
                media_path = package_root / "media" / f"{record['id']}{Path(record['relative_path']).suffix.lower()}"
                ET.SubElement(file, "pathurl").text = media_path.resolve().as_uri()
                file.append(ET.fromstring(ET.tostring(rate)))
                if role != "Picture":
                    am = ET.SubElement(ET.SubElement(file, "media"), "audio")
                    sc = ET.SubElement(am, "samplecharacteristics")
                    ET.SubElement(sc, "samplerate").text = str(record["sample_rate"])
        return b"<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!DOCTYPE xmeml>\n" + ET.tostring(xmeml, encoding="utf-8")

    def export_delivery(self, episode_id: str) -> dict:
        """Queue the immutable snapshot. Copying never blocks the HTTP request."""
        check = self.delivery_preflight(episode_id)
        if not check["allowed"]:
            raise DomainError("; ".join(check["blockers"]), 409)
        project = self.studio.project(check["project_id"])
        input_hash = self._delivery_hash(check)
        with self.studio.db.transaction() as conn:
            existing = conn.execute("SELECT * FROM delivery_packages WHERE episode_id=? AND input_hash=? AND state IN ('QUEUED','BUILDING','RECONCILING','FILES_READY_UNVERIFIED_RESOLVE') ORDER BY version DESC LIMIT 1", (episode_id, input_hash)).fetchone()
            if existing and (existing["state"] != "FILES_READY_UNVERIFIED_RESOLVE" or self._package_intact(project["root"], existing)):
                return dict(existing, inventory=json.loads(existing["inventory_json"]))
            version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM delivery_packages WHERE episode_id=?", (episode_id,)).fetchone()[0]
            relative = f"delivery/{episode_id}/v{version:03d}"
            item = {"id": uid(), "project_id": check["project_id"], "episode_id": episode_id,
                    "version": version, "relative_path": relative, "input_hash": input_hash,
                    "inventory_json": "{}", "format": check["format"], "state": "QUEUED", "created_at": now()}
            conn.execute("INSERT INTO delivery_packages VALUES(:id,:project_id,:episode_id,:version,:relative_path,:input_hash,:inventory_json,:format,:state,:created_at)", item)
            self.studio.audit(conn, check["project_id"], "delivery.queued", item["id"], {"version": version, "input_hash": input_hash})
        return item

    @staticmethod
    def _delivery_hash(check: dict) -> str:
        return digest({"profile": check["profile"], "fps": check["fps"], "format": check["format"],
                       "shots": [{"shot_id": x["shot_id"], "picture_lock": x["picture_lock"],
                                  "timeline": x["timeline"], "take": {k: x["take"][k] for k in ("id", "version", "sha256", "relative_path")},
                                  "stems": [{k: s[k] for k in ("id", "role", "version", "sha256", "relative_path", "offset_frame", "sample_count", "sample_rate")} for s in x["stems"]]} for x in check["shots"]]})

    def process_delivery(self) -> dict | None:
        with self.studio.db.transaction() as conn:
            row = conn.execute("SELECT * FROM delivery_packages WHERE state='QUEUED' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            item = dict(row)
            conn.execute("UPDATE delivery_packages SET state='BUILDING' WHERE id=?", (item["id"],))
        try:
            return self._build_delivery(item)
        except Exception:
            with self.studio.db.transaction() as conn:
                conn.execute("UPDATE delivery_packages SET state='PARTIAL_COPY' WHERE id=? AND state='BUILDING'", (item["id"],))
                self.studio.audit(conn, item["project_id"], "delivery.copy_incomplete", item["id"], {"note": "Output giữ nguyên; đối soát trước export version mới."})
            return dict(item, state="PARTIAL_COPY")

    def _build_delivery(self, item: dict) -> dict:
        episode_id, input_hash = item["episode_id"], item["input_hash"]
        check = self.delivery_preflight(episode_id)
        if not check["allowed"] or self._delivery_hash(check) != input_hash:
            with self.studio.db.transaction() as conn:
                conn.execute("UPDATE delivery_packages SET state='STALE' WHERE id=?", (item["id"],))
            return dict(item, state="STALE")
        project = self.studio.project(check["project_id"])
        sources = {r["id"]: r for x in check["shots"] for r in [x["take"], *x["stems"]]}
        root = Path(project["root"]).resolve()
        folder = root / item["relative_path"]
        folder.mkdir(parents=True, exist_ok=False)
        media = folder / "media"
        media.mkdir()
        inventory = []
        for record in sources.values():
            src_rel, src = self.video._checked_file(check["project_id"], record["relative_path"], record["sha256"])
            target_rel = f"media/{record['id']}{src.suffix.lower()}"
            target = folder / target_rel
            shutil.copyfile(src, target)
            if file_sha256(target) != record["sha256"]:
                raise DomainError("Media copy checksum sai; delivery BUILDING cần đối soát.", 409)
            inventory.append({"source_relative_path": src_rel, "relative_path": target_rel,
                              "sha256": record["sha256"], "bytes": target.stat().st_size, "record_id": record["id"]})
        manifest = {"schema_version": "p6.delivery.1", "format": check["format"], "fps": check["fps"],
                    "profile": check["profile"], "timecode_start": check["profile"]["timecode_start"], "shots": [{"code": item["shot_code"], "in": item["timeline"]["in"],
                    "out_exclusive": item["timeline"]["out_exclusive"], "picture_lock_id": item["picture_lock"]["id"],
                    "video_record_id": item["take"]["id"], "stems": [{"role": s["role"], "record_id": s["id"],
                    "offset_frame": s["offset_frame"]} for s in item["stems"]]} for item in check["shots"]],
                    "media": inventory, "input_hash": input_hash, "resolve_import_verified": False}
        (folder / "inventory.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        (folder / "timeline.xml").write_bytes(self._xml(check["shots"], check["fps"]["num"], folder, check["profile"]))
        (folder / "IMPORT-RESOLVE.txt").write_text(
            "Chưa kiểm chứng trên DaVinci Resolve. Import Timeline > AAF/EDL/XML, chọn timeline.xml; "
            "nếu đã di chuyển package, relink vào thư mục media/ bằng checksum trong inventory.json. "
            "Kiểm shot count, FPS, in/out, offset 5 stem, media thiếu rồi review master thủ công. "
            "Đường dẫn XML là đường dẫn tại nơi xuất; inventory chỉ dùng relative path.\n", encoding="utf-8")
        manifest["sidecars"] = [{"relative_path": name, "sha256": file_sha256(folder / name)} for name in ("timeline.xml", "IMPORT-RESOLVE.txt")]
        (folder / "inventory.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        stale = False
        with self.studio.db.transaction() as conn:
            fresh = self.delivery_preflight(episode_id)
            if not fresh["allowed"] or self._delivery_hash(fresh) != input_hash:
                conn.execute("UPDATE delivery_packages SET state='STALE' WHERE id=?", (item["id"],))
                stale = True
            else:
                conn.execute("UPDATE delivery_packages SET state='STALE' WHERE episode_id=? AND id!=? AND state='FILES_READY_UNVERIFIED_RESOLVE'", (episode_id, item["id"]))
                conn.execute("UPDATE delivery_packages SET state='FILES_READY_UNVERIFIED_RESOLVE',inventory_json=? WHERE id=?", (canonical(manifest), item["id"]))
                self.studio.audit(conn, check["project_id"], "delivery.exported", item["id"], {"version": item["version"], "media_count": len(inventory), "input_hash": input_hash})
        return dict(item, state="STALE" if stale else "FILES_READY_UNVERIFIED_RESOLVE", inventory=manifest)

    @staticmethod
    def _package_intact(root: str, package) -> bool:
        folder = Path(root).resolve() / package["relative_path"]
        try:
            manifest = json.loads(package["inventory_json"])
            if json.loads((folder / "inventory.json").read_text(encoding="utf-8")) != manifest:
                return False
            for record in [*manifest["media"], *manifest["sidecars"]]:
                path = (folder / record["relative_path"]).resolve()
                if not path.is_relative_to(folder.resolve()) or not path.is_file() or file_sha256(path) != record["sha256"]:
                    return False
            return (folder / "timeline.xml").is_file()
        except (KeyError, ValueError, TypeError, OSError):
            return False

    def packages(self, episode_id: str) -> list[dict]:
        with self.studio.db.connect() as conn:
            episode = require(conn.execute("SELECT project_id FROM episodes WHERE id=?", (episode_id,)).fetchone(), "episode")
            root = self.studio.project(episode["project_id"])["root"]
            return [dict(row, state="CORRUPT" if row["state"] == "FILES_READY_UNVERIFIED_RESOLVE" and not self._package_intact(root, row) else row["state"], inventory=json.loads(row["inventory_json"])) for row in conn.execute("SELECT * FROM delivery_packages WHERE episode_id=? ORDER BY version DESC", (episode_id,))]

    def reconcile_delivery(self, package_id: str) -> dict:
        with self.studio.db.connect() as conn:
            package = require(conn.execute("SELECT * FROM delivery_packages WHERE id=?", (package_id,)).fetchone(), "delivery package")
        if package["state"] in ("QUEUED", "BUILDING"):
            raise DomainError("Package còn chờ/đang sao chép; chưa được đối soát đồng thời.", 409)
        check = self.delivery_preflight(package["episode_id"])
        state, manifest = "PARTIAL_COPY", {}
        root = self.studio.project(package["project_id"])["root"]
        folder = Path(root) / package["relative_path"]
        if not check["allowed"] or self._delivery_hash(check) != package["input_hash"]:
            state = "STALE"
        else:
            try:
                manifest = json.loads((folder / "inventory.json").read_text(encoding="utf-8"))
                sources = {r["id"]: r for shot in check["shots"] for r in [shot["take"], *shot["stems"]]}
                records = manifest["media"]
                matches = len(records) == len(sources) and {r["record_id"] for r in records} == set(sources) and all(r["sha256"] == sources[r["record_id"]]["sha256"] and r["relative_path"] == f"media/{r['record_id']}{Path(sources[r['record_id']]['relative_path']).suffix.lower()}" for r in records)
                if manifest["input_hash"] == package["input_hash"] and matches and self._package_intact(root, dict(package, inventory_json=canonical(manifest))) and (folder / "timeline.xml").read_bytes() == self._xml(check["shots"], check["fps"]["num"], folder, check["profile"]):
                    state = "FILES_READY_UNVERIFIED_RESOLVE"
                else:
                    state = "CORRUPT"
            except (OSError, ValueError, KeyError, TypeError):
                state = "PARTIAL_COPY"
        with self.studio.db.transaction() as conn:
            fresh = self.delivery_preflight(package["episode_id"])
            if not fresh["allowed"] or self._delivery_hash(fresh) != package["input_hash"]:
                state = "STALE"
            conn.execute("UPDATE delivery_packages SET state=?,inventory_json=? WHERE id=?", (state, canonical(manifest), package_id))
            self.studio.audit(conn, package["project_id"], "delivery.reconciled", package_id, {"state": state, "input_hash": package["input_hash"]})
        return dict(package, state=state, inventory=manifest)
