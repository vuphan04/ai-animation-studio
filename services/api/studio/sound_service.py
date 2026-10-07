"""Workflow V4.5 step 12 and checkpoint C9: Foley and sound on the locked picture.

Dialogue stays the ElevenLabs takes approved at C4, placed by the approved timing. Foley is made per shot by
the local HunyuanVideo-Foley tool (or brought in by hand); SFX, ambience and music are files the operator
brings in. Every clip is placed relative to a shot, so it moves with that shot when timing changes. A build
writes one stem per kind, their plain sum as a reference mix, and a review copy with the locked picture.
"""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

from . import gpu_queue, workflow
from .domain import DomainError, canonical, digest, file_sha256
from .foley_runner import FoleyToolSettings, HunyuanFoleyRunner
from .media_tools import SOUND_RATE, MediaTools
from .route_service import RouteService
from .service import now, require, uid
from .timing_domain import to_samples, to_seconds


KINDS = ("FOLEY", "SFX", "AMBIENCE", "MUSIC")
STEMS = (("DX", "Thoại"), ("FOLEY", "Foley"), ("SFX", "SFX"), ("AMB", "Ambience"), ("MUSIC", "Nhạc"))
STEM_OF = {"FOLEY": "FOLEY", "SFX": "SFX", "AMBIENCE": "AMB", "MUSIC": "MUSIC"}
CHECKS = ("dialogue", "sync", "balance", "clean")
AUDIO_SUFFIXES = (".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aif", ".aiff")
MIN_GAIN_DB, MAX_GAIN_DB = -60.0, 12.0
MAX_PROMPT = 500


class SoundService:
    def __init__(self, studio, breakdown, rough, timing, *, runner=None, tool=None, media=None):
        self.studio = studio
        self.db = studio.db
        self.breakdown = breakdown
        self.rough = rough
        self.timing = timing
        self.tool = tool or FoleyToolSettings(studio)
        self.runner = runner or HunyuanFoleyRunner(self.tool)
        self.media = media or MediaTools(studio)
        with self.db.transaction() as conn:
            # A model process started before the restart may still hold the GPU; a stem build can simply be run again.
            conn.execute("UPDATE episode_foley_jobs SET state='RECONCILING',error='Backend khởi động lại trong lúc tạo Foley; đóng tiến trình Foley cũ rồi xác nhận.',updated_at=? WHERE state='RUNNING'", (now(),))
            conn.execute("UPDATE episode_sound_jobs SET state='BLOCKED',error='Backend khởi động lại trong lúc ghép stem; ghép lại.',updated_at=? WHERE state='RUNNING'", (now(),))

    # ---- state -----------------------------------------------------------------------------------------------------

    def _state(self, conn, episode: dict, rough: dict | None = None) -> dict:
        rough = rough or self.rough._state(conn, episode)
        tool = self.tool.get()
        blockers = list(rough["blockers"])
        if rough["ready"] and not (rough["c7"] and rough["c7"]["current"]):
            blockers.append("C7: tập chưa khóa dựng; âm thanh chỉ làm trên hình đã khóa.")
        base = {"episode": episode, "ready": not blockers, "blockers": blockers, "shots": [], "clips": [], "build": None, "job": None, "c9": None,
                "ffmpeg_configured": self.media.available(), "foley_tool": {k: tool[k] for k in ("configured", "error")}, "issues": [],
                "summary": {"shots": 0, "foley": 0, "sfx": 0, "ambience": 0, "music": 0, "lines": 0}, "approve_blockers": list(blockers), "allowed": False}
        if blockers:
            return base
        plan, root, fps = rough["_plan"], rough["_root"], rough["fps"]
        total = plan["total_frames"]
        samples = to_samples(total, SOUND_RATE, fps)
        cuts = {cut["shot_id"]: cut for scene in rough["scenes"] for cut in scene["shots"]}
        basis = {shot_id: digest({"source": cut["source"]["sha256"], "frames": cut["length_frames"]}) for shot_id, cut in cuts.items()}
        jobs = {}
        for row in conn.execute("SELECT id,shot_id,state,error FROM episode_foley_jobs WHERE episode_id=? ORDER BY created_at", (episode["id"],)):
            jobs[row["shot_id"]] = dict(row)
        clips, render, used, problems = [], {kind: [] for kind in KINDS}, [], []
        for row in conn.execute("SELECT * FROM episode_sound_clips WHERE episode_id=? ORDER BY kind,created_at", (episode["id"],)):
            anchor = plan["by_id"].get(row["shot_id"])
            duration = max(1, math.ceil(row["duration_ms"] * fps["num"] / (1000 * fps["den"])))
            issues = []
            if not anchor:
                issues.append(f"Shot neo {row['shot_id']} không còn trong tập; chọn shot khác hoặc xóa clip.")
            elif row["offset_frames"] >= anchor["length_frames"]:
                issues.append("Vị trí bắt đầu nằm ngoài shot sau khi timing đổi; đặt lại vị trí.")
            if not (root / row["relative_path"]).is_file():
                issues.append("File âm thanh thiếu trong thư mục dự án.")
            stale = row["kind"] == "FOLEY" and bool(anchor) and row["basis_hash"] != basis[row["shot_id"]]
            if stale:
                issues.append("Hình của shot đã đổi sau khi tạo Foley; tạo lại, hoặc nghe lại và xác nhận còn khớp.")
            length = min(row["length_frames"] or duration, duration)
            if row["kind"] == "FOLEY" and anchor:
                length = min(length, anchor["length_frames"] - min(row["offset_frames"], anchor["length_frames"] - 1))
            start = anchor["start_frame"] + row["offset_frames"] if anchor else None
            item = {"id": row["id"], "kind": row["kind"], "label": row["label"], "shot_id": row["shot_id"], "offset_frames": row["offset_frames"],
                    "length_frames": row["length_frames"], "play_frames": length, "gain_db": row["gain_mdb"] / 1000, "fade_in_frames": row["fade_in_frames"],
                    "fade_out_frames": row["fade_out_frames"], "provenance": row["provenance"], "prompt": row["prompt"], "duration_seconds": round(row["duration_ms"] / 1000, 2),
                    "start_frame": start, "start_seconds": to_seconds(start, fps) if start is not None else None, "enabled": bool(row["enabled"]), "stale": stale,
                    "issues": issues, "revision": row["revision"], "created_at": row["created_at"]}
            clips.append(item)
            if item["enabled"] and issues:
                problems.append(f"{row['label']}: {issues[0]}")
            if item["enabled"] and not issues:
                size = to_samples(length, SOUND_RATE, fps)
                render[row["kind"]].append({"path": root / row["relative_path"], "start_sample": to_samples(start, SOUND_RATE, fps), "length_samples": size,
                                            "fade_in_samples": min(size, to_samples(row["fade_in_frames"], SOUND_RATE, fps)),
                                            "fade_out_samples": min(size, to_samples(row["fade_out_frames"], SOUND_RATE, fps)), "gain_db": row["gain_mdb"] / 1000,
                                            "sha256": row["sha256"], "label": row["label"]})
                used.append([row["id"], row["kind"], row["sha256"], start, length, row["gain_mdb"], row["fade_in_frames"], row["fade_out_frames"]])
        shots = []
        for shot in plan["shots"]:
            cut = cuts[shot["shot_id"]]
            segment = self.rough._segment(root, episode["code"], cut, fps, rough["size"])
            shots.append({"shot_id": shot["shot_id"], "scene_key": shot["scene_key"], "route": shot["route"], "description": shot["description"],
                          "start_frame": shot["start_frame"], "start_seconds": to_seconds(shot["start_frame"], fps), "length_frames": shot["length_frames"],
                          "seconds": shot["seconds"], "default_prompt": (shot["description_en"] or shot["prompt_en"] or "")[:MAX_PROMPT], "has_picture": segment.is_file(),
                          "foley": [clip for clip in clips if clip["kind"] == "FOLEY" and clip["shot_id"] == shot["shot_id"]][::-1], "foley_job": jobs.get(shot["shot_id"]),
                          "_segment": segment, "_basis": basis[shot["shot_id"]]})
        dialogue = [[line["line_id"], line["sha256"], shot["start_frame"] + line["start_frame"]] for shot in plan["shots"] for line in shot["lines"]]
        input_hash = digest({"fps": fps, "frames": total, "rate": SOUND_RATE, "picture": rough["lock_hash"], "dialogue": dialogue, "clips": sorted(used)})
        row = conn.execute("SELECT * FROM episode_sound_builds WHERE episode_id=? ORDER BY version DESC LIMIT 1", (episode["id"],)).fetchone()
        build = None
        if row:
            stems = json.loads(row["stems_json"])
            build = {"id": row["id"], "version": row["version"], "created_at": row["created_at"], "loudness": json.loads(row["loudness_json"]),
                     "stems": [{"role": role, "label": label} for role, label in STEMS], "seconds": to_seconds(total, fps),
                     "file_ok": all((root / item["relative_path"]).is_file() for item in stems.values()),
                     "current": row["input_hash"] == input_hash, "_stems": stems, "_dir": row["relative_dir"], "_input_hash": row["input_hash"]}
        job = conn.execute("SELECT id,state,error,updated_at FROM episode_sound_jobs WHERE episode_id=? ORDER BY created_at DESC LIMIT 1", (episode["id"],)).fetchone()
        usable = bool(build and build["current"] and build["file_ok"])
        latest = RouteService._checkpoint(conn, episode, "C9")
        c9 = {"sequence": latest["exact_version"], "created_at": latest["created_at"], "current": usable and latest["dependency_hash"] == input_hash} if latest else None
        approve = list(problems[:6])
        if not usable:
            approve.append("Chưa ghép stem theo đúng âm thanh và hình hiện tại.")
        with_foley = {clip["shot_id"] for clip in clips if clip["kind"] == "FOLEY" and clip["enabled"] and not clip["issues"]}
        count = lambda kind: sum(clip["kind"] == kind and clip["enabled"] for clip in clips)
        return {**base, "fps": fps, "total_frames": total, "seconds": to_seconds(total, fps), "shots": shots, "clips": [clip for clip in clips if clip["kind"] != "FOLEY"],
                "build": build, "job": dict(job) if job else None, "c9": c9, "input_hash": input_hash, "issues": problems, "approve_blockers": approve, "allowed": not approve,
                "summary": {"shots": len(shots), "foley": len(with_foley), "sfx": count("SFX"), "ambience": count("AMBIENCE"), "music": count("MUSIC"), "lines": len(dialogue)},
                "notes": {"mix": "Mix ở đây là tổng các stem, chưa nén hay chuẩn hóa loudness; mix hoàn chỉnh làm trong DaVinci Resolve (C10).",
                          "foley": "Foley tạo bằng HunyuanVideo-Foley chạy trên máy này, xếp hàng chung GPU với Unreal; không chạy song song."},
                "_plan": plan, "_root": root, "_render": render, "_samples": samples, "_rough": rough}

    @staticmethod
    def _public(state: dict) -> dict:
        strip = lambda item: {k: v for k, v in item.items() if not k.startswith("_")}
        return {**strip(state), "shots": [strip(shot) for shot in state["shots"]], "build": strip(state["build"]) if state["build"] else None}

    def state(self, episode_id: str) -> dict:
        with self.db.connect() as conn:
            return self._public(self._state(conn, self.breakdown._episode(conn, episode_id)))

    def _ready(self, conn, episode_id: str) -> tuple[dict, dict]:
        episode = self.breakdown._episode(conn, episode_id)
        state = self._state(conn, episode)
        if not state["ready"]:
            raise DomainError("Chưa làm được âm thanh: " + " ".join(state["blockers"]), 409)
        return episode, state

    # ---- clips -----------------------------------------------------------------------------------------------------

    def _store(self, conn, episode: dict, kind: str, shot: dict, source: Path, provenance: str, *, prompt: str | None = None, job_id: str | None = None) -> dict:
        """Copy an audio file into the project as a clip anchored at the start of the shot."""
        info = self.media.probe_audio(source)
        root = Path(self.studio.project(episode["project_id"])["root"]).resolve()
        clip_id = uid()
        number = conn.execute("SELECT COUNT(*)+1 FROM episode_sound_clips WHERE episode_id=? AND kind=? AND shot_id=?", (episode["id"], kind, shot["shot_id"])).fetchone()[0]
        relative = (f"audio/foley/{episode['code']}/{shot['shot_id']}/foley-{clip_id[:8]}{source.suffix.lower()}" if kind == "FOLEY"
                    else f"audio/clips/{episode['code']}/{kind.lower()}/{clip_id[:8]}{source.suffix.lower()}")
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        label = f"Foley {shot['shot_id']} · bản {number}" if kind == "FOLEY" else source.stem[:80]
        # A Foley take waits for the operator to listen to it; the other kinds are in the build as soon as they are placed.
        conn.execute("INSERT INTO episode_sound_clips(id,project_id,episode_id,kind,shot_id,offset_frames,length_frames,gain_mdb,fade_in_frames,fade_out_frames,label,provenance,prompt,job_id,basis_hash,relative_path,sha256,bytes,sample_rate,channels,duration_ms,enabled,revision,created_at,updated_at) "
                     "VALUES(?,?,?,?,?,0,NULL,0,0,0,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
                     (clip_id, episode["project_id"], episode["id"], kind, shot["shot_id"], label, provenance, prompt, job_id, shot["_basis"] if kind == "FOLEY" else None,
                      relative, file_sha256(target), target.stat().st_size, info["sample_rate"], info["channels"], info["duration_ms"], 0 if kind == "FOLEY" else 1, now(), now()))
        return {"id": clip_id, "label": label}

    def import_clip(self, episode_id: str, kind: str, shot_id: str, source_path: str) -> dict:
        source = Path(source_path)
        if kind not in KINDS:
            raise DomainError("Loại âm thanh không hợp lệ.")
        if not source.is_file() or source.suffix.lower() not in AUDIO_SUFFIXES:
            raise DomainError("Chọn file âm thanh WAV, MP3, FLAC, M4A, OGG hoặc AIFF có trên máy này.")
        self.media.paths()
        with self.db.transaction() as conn:
            episode, state = self._ready(conn, episode_id)
            shot = require(next((s for s in state["shots"] if s["shot_id"] == shot_id), None), "shot")
            clip = self._store(conn, episode, kind, shot, source, "OPERATOR_FILE")
            self.studio.audit(conn, episode["project_id"], "sound.clip_imported", episode_id, {"kind": kind, "shot": shot_id})
        return {**self.state(episode_id), "clip_id": clip["id"]}

    def _clip(self, conn, clip_id: str, expected_revision: int):
        row = require(conn.execute("SELECT * FROM episode_sound_clips WHERE id=?", (clip_id,)).fetchone(), "sound clip")
        if row["revision"] != expected_revision:
            raise DomainError("Clip đã đổi ở phiên khác; tải lại.", 409)
        return row

    def update_clip(self, clip_id: str, expected_revision: int, shot_id: str, offset_frames: int, length_frames: int | None, gain_db: float,
                    fade_in_frames: int, fade_out_frames: int, enabled: bool) -> dict:
        with self.db.transaction() as conn:
            row = self._clip(conn, clip_id, expected_revision)
            episode, state = self._ready(conn, row["episode_id"])
            shot = next((s for s in state["shots"] if s["shot_id"] == shot_id), None)
            if not shot or (row["kind"] == "FOLEY" and shot_id != row["shot_id"]):
                raise DomainError("Chọn shot neo có trong tập; Foley luôn thuộc shot nó được tạo cho.", 409)
            numbers = (offset_frames, fade_in_frames, fade_out_frames) + ((length_frames,) if length_frames is not None else ())
            if any(type(value) is not int or value < 0 for value in numbers) or (length_frames is not None and length_frames < 1):
                raise DomainError("Vị trí, độ dài và fade tính bằng số frame nguyên, không âm.")
            if offset_frames >= shot["length_frames"]:
                raise DomainError(f"Vị trí bắt đầu phải nằm trong shot neo (0 đến {shot['length_frames'] - 1} frame).")
            if isinstance(gain_db, bool) or not isinstance(gain_db, (int, float)) or not MIN_GAIN_DB <= gain_db <= MAX_GAIN_DB:
                raise DomainError(f"Mức âm lượng từ {MIN_GAIN_DB:g} đến +{MAX_GAIN_DB:g} dB.")
            fps = state["fps"]
            available = length_frames or max(1, math.ceil(row["duration_ms"] * fps["num"] / (1000 * fps["den"])))
            if fade_in_frames + fade_out_frames > available:
                raise DomainError("Tổng fade vào và fade ra dài hơn đoạn phát của clip.")
            if enabled and row["kind"] == "FOLEY":
                # One Foley take per shot is in use; choosing this one is the operator's approval of it.
                conn.execute("UPDATE episode_sound_clips SET enabled=0,revision=revision+1,updated_at=? WHERE episode_id=? AND kind='FOLEY' AND shot_id=? AND id!=? AND enabled=1",
                             (now(), row["episode_id"], row["shot_id"], clip_id))
            conn.execute("UPDATE episode_sound_clips SET shot_id=?,offset_frames=?,length_frames=?,gain_mdb=?,fade_in_frames=?,fade_out_frames=?,enabled=?,revision=revision+1,updated_at=? WHERE id=?",
                         (shot_id, offset_frames, length_frames, round(gain_db * 1000), fade_in_frames, fade_out_frames, int(bool(enabled)), now(), clip_id))
            self.studio.audit(conn, episode["project_id"], "sound.clip_updated", row["episode_id"], {"clip": clip_id, "kind": row["kind"]})
        return self.state(row["episode_id"])

    def keep_foley(self, clip_id: str, expected_revision: int) -> dict:
        """The picture of the shot changed, the operator listened again and the Foley still fits."""
        with self.db.transaction() as conn:
            row = self._clip(conn, clip_id, expected_revision)
            episode, state = self._ready(conn, row["episode_id"])
            shot = next((s for s in state["shots"] if s["shot_id"] == row["shot_id"]), None)
            if row["kind"] != "FOLEY" or not shot:
                raise DomainError("Chỉ Foley của shot còn trong tập mới xác nhận lại được.", 409)
            conn.execute("UPDATE episode_sound_clips SET basis_hash=?,revision=revision+1,updated_at=? WHERE id=?", (shot["_basis"], now(), clip_id))
            self.studio.audit(conn, episode["project_id"], "sound.foley_kept", row["episode_id"], {"clip": clip_id, "shot": row["shot_id"]})
        return self.state(row["episode_id"])

    def delete_clip(self, clip_id: str, expected_revision: int) -> dict:
        with self.db.transaction() as conn:
            row = self._clip(conn, clip_id, expected_revision)
            conn.execute("DELETE FROM episode_sound_clips WHERE id=?", (clip_id,))
            self.studio.audit(conn, row["project_id"], "sound.clip_deleted", row["episode_id"], {"clip": clip_id, "kind": row["kind"]})
        # The file is the app's own copy; the operator's original stays where it was.
        (Path(self.studio.project(row["project_id"])["root"]).resolve() / row["relative_path"]).unlink(missing_ok=True)
        return self.state(row["episode_id"])

    def clip_file(self, clip_id: str) -> Path:
        with self.db.connect() as conn:
            row = require(conn.execute("SELECT * FROM episode_sound_clips WHERE id=?", (clip_id,)).fetchone(), "sound clip")
        path = Path(self.studio.project(row["project_id"])["root"]).resolve() / row["relative_path"]
        if not path.is_file():
            raise DomainError("File âm thanh thiếu trong thư mục dự án.", 409)
        return path

    # ---- Foley -----------------------------------------------------------------------------------------------------

    def enqueue_foley(self, episode_id: str, shot_id: str, prompt: str) -> dict:
        if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= MAX_PROMPT:
            raise DomainError(f"Mô tả âm thanh của shot (tiếng Anh) dài từ 1 đến {MAX_PROMPT} ký tự.")
        tool = self.tool.get()
        if not tool["configured"]:
            raise DomainError(tool["error"] or "Cấu hình HunyuanVideo-Foley ở Cài đặt → Foley local, hoặc nhập Foley làm tay.", 409)
        self.media.paths()
        with self.db.transaction() as conn:
            episode, state = self._ready(conn, episode_id)
            shot = require(next((s for s in state["shots"] if s["shot_id"] == shot_id), None), "shot")
            if not shot["has_picture"]:
                raise DomainError("Thiếu đoạn rough cut của shot; ghép lại rough cut của scene ở tab Dựng shot.", 409)
            if shot["foley_job"] and shot["foley_job"]["state"] in ("QUEUED", "RUNNING", "RECONCILING"):
                raise DomainError("Lần tạo Foley trước bị gián đoạn; xác nhận trước khi tạo lại." if shot["foley_job"]["state"] == "RECONCILING" else "Đang tạo Foley cho shot này.", 409)
            request = {"schema_version": "foley.request.1", "episode_id": episode_id, "shot_id": shot_id, "prompt": prompt.strip(), "basis_hash": shot["_basis"],
                       "runner": self.runner.kind}
            input_hash = digest(request)
            operation_id = digest({"input_hash": input_hash, "nonce": uid()})
            conn.execute("INSERT INTO episode_foley_jobs(id,project_id,episode_id,shot_id,operation_id,input_hash,request_json,state,error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'QUEUED',NULL,?,?)",
                         (uid(), episode["project_id"], episode_id, shot_id, operation_id, input_hash, canonical(request), now(), now()))
            self.studio.audit(conn, episode["project_id"], "sound.foley_queued", episode_id, {"shot": shot_id})
        return self.state(episode_id)

    def process_next_foley(self) -> dict | None:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM episode_foley_jobs WHERE state='QUEUED' ORDER BY created_at LIMIT 1").fetchone()
            if not row or gpu_queue.holder(conn):
                return None
            job = dict(row)
            gpu_queue.try_acquire(conn, job["operation_id"], "FOLEY")
            conn.execute("UPDATE episode_foley_jobs SET state='RUNNING',updated_at=? WHERE id=?", (now(), job["id"]))
        request = json.loads(job["request_json"])
        work = self.db.data_root / "foley-work" / job["operation_id"]
        state_after, error = "COMPLETED", None
        try:
            with self.db.connect() as conn:
                episode, state = self._ready(conn, job["episode_id"])
            shot = next((s for s in state["shots"] if s["shot_id"] == request["shot_id"]), None)
            if not shot or shot["_basis"] != request["basis_hash"] or not shot["has_picture"]:
                raise DomainError("Hình của shot đã đổi sau khi xếp việc tạo Foley; xếp lại.", 409)
            produced = self.runner.generate(shot["_segment"], request["prompt"], work / "out")
            if produced.suffix.lower() not in AUDIO_SUFFIXES:
                self.media.extract_audio(produced, work / "foley.wav")
                produced = work / "foley.wav"
            with self.db.transaction() as conn:
                self._store(conn, episode, "FOLEY", shot, produced, self.runner.kind, prompt=request["prompt"], job_id=job["id"])
        except Exception as exc:
            state_after = "BLOCKED"
            error = str(exc)[:500] if isinstance(exc, DomainError) else f"{type(exc).__name__}: không tạo được Foley."
        finally:
            shutil.rmtree(work, ignore_errors=True)
        with self.db.transaction() as conn:
            conn.execute("UPDATE episode_foley_jobs SET state=?,error=?,updated_at=? WHERE id=?", (state_after, error, now(), job["id"]))
            gpu_queue.release(conn, job["operation_id"])
            self.studio.audit(conn, job["project_id"], "sound.foley_" + state_after.lower(), job["id"], {"shot": job["shot_id"]})
            return dict(conn.execute("SELECT id,shot_id,state,error FROM episode_foley_jobs WHERE id=?", (job["id"],)).fetchone())

    def release_foley(self, job_id: str, worker_stopped: bool) -> dict:
        with self.db.transaction() as conn:
            job = require(conn.execute("SELECT * FROM episode_foley_jobs WHERE id=?", (job_id,)).fetchone(), "Foley job")
            if job["state"] != "RECONCILING":
                raise DomainError("Việc tạo Foley này không ở trạng thái cần xác nhận.", 409)
            if not worker_stopped:
                raise DomainError("Đóng tiến trình HunyuanVideo-Foley của lần chạy cũ rồi xác nhận trong giao diện.", 409)
            conn.execute("UPDATE episode_foley_jobs SET state='BLOCKED',error='Bị gián đoạn; tạo lại khi cần.',updated_at=? WHERE id=?", (now(), job_id))
            gpu_queue.release(conn, job["operation_id"])
        return self.state(job["episode_id"])

    # ---- stems -----------------------------------------------------------------------------------------------------

    def enqueue_build(self, episode_id: str) -> dict:
        self.media.paths()
        with self.db.transaction() as conn:
            episode, state = self._ready(conn, episode_id)
            if state["issues"]:
                raise DomainError("Sửa các clip đang lỗi trước khi ghép stem: " + " · ".join(state["issues"][:4]), 409)
            if state["build"] and state["build"]["current"] and state["build"]["file_ok"]:
                raise DomainError("Stem hiện tại đã đúng với âm thanh và hình đang dùng.", 409)
            if conn.execute("SELECT 1 FROM episode_sound_jobs WHERE episode_id=? AND state IN ('QUEUED','RUNNING')", (episode_id,)).fetchone():
                raise DomainError("Đang ghép stem cho tập này.", 409)
            conn.execute("INSERT INTO episode_sound_jobs(id,project_id,episode_id,operation_id,input_hash,state,error,created_at,updated_at) VALUES(?,?,?,?,?,'QUEUED',NULL,?,?)",
                         (uid(), episode["project_id"], episode_id, digest({"input_hash": state["input_hash"], "nonce": uid()}), state["input_hash"], now(), now()))
        return self.state(episode_id)

    def process_next_build(self) -> dict | None:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM episode_sound_jobs WHERE state='QUEUED' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            job = dict(row)
            conn.execute("UPDATE episode_sound_jobs SET state='RUNNING',updated_at=? WHERE id=?", (now(), job["id"]))
        work = self.db.data_root / "sound-work" / job["operation_id"]
        folder = None
        try:
            with self.db.connect() as conn:
                episode, state = self._ready(conn, job["episode_id"])
                if state["input_hash"] != job["input_hash"] or state["issues"]:
                    raise DomainError("Âm thanh hoặc hình đã đổi sau khi xếp việc ghép stem; ghép lại.", 409)
                timing = self.timing._state(conn, episode)
                version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM episode_sound_builds WHERE episode_id=?", (job["episode_id"],)).fetchone()[0]
            root, code, samples, fps = state["_root"], episode["code"], state["_samples"], state["fps"]
            relative_dir = f"audio/stems/{code}/v{version}"
            folder = root / relative_dir
            if folder.exists():
                raise DomainError("Đã có thư mục stem cùng phiên bản trong dự án; kiểm tra thư mục audio/stems.", 409)
            timed = timing["_stored"]["shots"]
            dialogue = []
            if any(shot["lines"] for shot in timed):
                self.timing._mix(root, work / "dialogue.wav", timed, fps, {take["id"]: take for take in timing["_inputs"]["selected"].values()})
                dialogue = [{"path": work / "dialogue.wav", "start_sample": 0}]
            paths = {role: folder / f"{code}_{role}.wav" for role, _ in STEMS}
            self.media.render_stem(dialogue, paths["DX"], samples)
            for kind in KINDS:
                self.media.render_stem(state["_render"][kind], paths[STEM_OF[kind]], samples)
            mix = folder / f"{code}_MIX.wav"
            self.media.render_stem([{"path": path, "start_sample": 0} for path in paths.values()], mix, samples)
            for path in (*paths.values(), mix):
                found = self.media.stem_info(path)["samples"]
                if found != samples:
                    raise DomainError(f"{path.name} dài {found} mẫu, hình đã khóa cần {samples} mẫu; không dùng bản này.", 409)
            segments = [shot["_segment"] for shot in state["shots"]]
            if not all(segment.is_file() for segment in segments):
                raise DomainError("Thiếu đoạn rough cut của một số shot; ghép lại rough cut ở tab Dựng shot rồi ghép stem.", 409)
            review = folder / f"{code}_sound-review.mp4"
            self.media.join(segments, mix, review, work)
            stems = {name: {"relative_path": f"{relative_dir}/{path.name}", "sha256": file_sha256(path), "bytes": path.stat().st_size}
                     for name, path in (*paths.items(), ("MIX", mix), ("REVIEW", review))}
            loudness = self.media.loudness(mix)
            with self.db.transaction() as conn:
                conn.execute("INSERT INTO episode_sound_builds(id,project_id,episode_id,version,input_hash,relative_dir,stems_json,loudness_json,sample_rate,sample_count,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                             (uid(), job["project_id"], job["episode_id"], version, job["input_hash"], relative_dir, canonical(stems), canonical(loudness), SOUND_RATE, samples, now()))
                conn.execute("UPDATE episode_sound_jobs SET state='COMPLETED',error=NULL,updated_at=? WHERE id=?", (now(), job["id"]))
                self.studio.audit(conn, job["project_id"], "sound.built", job["episode_id"], {"version": version, "samples": samples})
        except Exception as exc:
            if folder:
                shutil.rmtree(folder, ignore_errors=True)
            with self.db.transaction() as conn:
                conn.execute("UPDATE episode_sound_jobs SET state='BLOCKED',error=?,updated_at=? WHERE id=?",
                             (str(exc)[:500] if isinstance(exc, DomainError) else f"{type(exc).__name__}: không ghép được stem.", now(), job["id"]))
        finally:
            shutil.rmtree(work, ignore_errors=True)
        with self.db.connect() as conn:
            return dict(conn.execute("SELECT id,state,error,updated_at FROM episode_sound_jobs WHERE id=?", (job["id"],)).fetchone())

    def build_file(self, build_id: str, role: str) -> Path:
        with self.db.connect() as conn:
            row = require(conn.execute("SELECT * FROM episode_sound_builds WHERE id=?", (build_id,)).fetchone(), "sound build")
        item = json.loads(row["stems_json"]).get(role)
        path = Path(self.studio.project(row["project_id"])["root"]).resolve() / item["relative_path"] if item else None
        if not path or not path.is_file():
            raise DomainError("File stem thiếu trong thư mục dự án; ghép lại stem.", 409)
        return path

    # ---- C9 --------------------------------------------------------------------------------------------------------

    def approve_c9(self, episode_id: str, expected_hash: str, checks: dict) -> dict:
        if not isinstance(checks, dict) or set(checks) != set(CHECKS) or any(checks[key] is not True for key in CHECKS):
            raise DomainError("Xác nhận đủ bốn mục đã nghe trên bản xem thử: thoại rõ, âm thanh khớp hình, cân bằng các lớp, không rè hay vỡ tiếng.")
        with self.db.transaction() as conn:
            episode, state = self._ready(conn, episode_id)
            if state["approve_blockers"]:
                raise DomainError("Chưa duyệt được C9: " + " · ".join(state["approve_blockers"][:6]), 409)
            if state["input_hash"] != expected_hash:
                raise DomainError("Âm thanh đã đổi sau khi bạn nghe; tải lại.", 409)
            if state["c9"] and state["c9"]["current"]:
                raise DomainError("Âm thanh này đã được duyệt.", 409)
            build = state["build"]
            for item in build["_stems"].values():
                if file_sha256(state["_root"] / item["relative_path"]) != item["sha256"]:
                    raise DomainError("File stem đã bị đổi ngoài app; ghép lại stem.", 409)
            workflow.record_checkpoint(self.studio, conn, episode["project_id"], "C9", episode_id, (state["c9"]["sequence"] if state["c9"] else 0) + 1, expected_hash,
                                       {"build_version": build["version"], "stems": {role: item["sha256"] for role, item in build["_stems"].items() if role != "REVIEW"},
                                        "loudness": build["loudness"], "sample_rate": SOUND_RATE, "samples": state["_samples"], "counts": state["summary"],
                                        "picture_lock_hash": state["_rough"]["lock_hash"], "checks": sorted(CHECKS)})
        return self.state(episode_id)

    def approved_build(self, state: dict) -> dict | None:
        """For delivery: the stems of the build that passed C9, while they still match sound and picture."""
        return state["build"] if state.get("c9") and state["c9"]["current"] else None
