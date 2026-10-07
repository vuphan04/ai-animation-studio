"""Workflow V4.5 step 09 / C6 of the UE branch: simulation cache declarations and the proxy render.

Baking hair, cloth and VFX caches is done by the operator in Unreal; the app records what was declared for
each shot and what Unreal reports about cache tracks. The proxy is a low-resolution render used only for
the rough cut. No final is rendered here.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import gpu_queue, workflow
from .domain import DomainError, canonical, digest, file_sha256, UE_TARGET_VERSION
from .media_tools import MediaTools
from .service import now, require, uid


SUBSYSTEMS = ("HAIR", "CLOTH", "VFX")


def prepared_shots(result: object, request: dict) -> dict:
    """Check what tools/unreal/episode_render.py reported for a render request; returns its per-shot results."""
    if not isinstance(result, dict) or result.get("schema_version") != "v45.ue-render-result.1" or result.get("adapter_kind") != "UNREAL_EDITOR_PYTHON" \
            or result.get("operation_id") != request["operation_id"] or result.get("status") != "PREPARED" or not isinstance(result.get("shots"), list):
        raise DomainError("Kết quả chuẩn bị render từ Unreal sai schema, nguồn hoặc operation.", 409)
    if not isinstance(result.get("engine_version"), str) or not result["engine_version"].startswith(UE_TARGET_VERSION + "."):
        raise DomainError(f"App viết cho Unreal {UE_TARGET_VERSION}; lần render báo phiên bản {result.get('engine_version')!r}.", 409)
    prepared = {s.get("shot_id"): s for s in result["shots"] if isinstance(s, dict)}
    if set(prepared) != {s["shot_id"] for s in request["shots"]}:
        raise DomainError("Unreal báo kết quả cho danh sách shot khác với yêu cầu.", 409)
    return prepared
SUBSYSTEM_LABELS = {"HAIR": "tóc", "CLOTH": "vải", "VFX": "VFX"}
PROXY_HEIGHT = 540


def proxy_size(conn, project_id: str) -> tuple[int, int]:
    """Half-HD review size with the aspect ratio of the series standard, even on both sides."""
    row = conn.execute("SELECT width,height FROM series_standards WHERE project_id=?", (project_id,)).fetchone()
    width, height = (row["width"], row["height"]) if row else (1920, 1080)
    return max(2, round(PROXY_HEIGHT * width / height / 2) * 2), PROXY_HEIGHT


class UeProxyService:
    def __init__(self, studio, breakdown, ue, ai, *, preparer=None, renderer=None, media=None):
        self.studio = studio
        self.db = studio.db
        self.breakdown = breakdown
        self.ue = ue
        self.ai = ai
        self.preparer = preparer or (lambda request: studio._unreal_adapter().prepare_render(request))
        self.renderer = renderer or (lambda manifest: studio._unreal_adapter().render_queue(manifest))
        self.media = media or MediaTools(studio)
        with self.db.transaction() as conn:
            conn.execute("UPDATE ue_proxy_jobs SET state='RECONCILING',error='Backend khởi động lại trong lúc Unreal đang render proxy; đóng Unreal cũ rồi xác nhận.',updated_at=? WHERE state='RUNNING'", (now(),))

    # ---- state -----------------------------------------------------------------------------------------------------

    def _state(self, conn, episode: dict) -> dict:
        ue = self.ue._state(conn, episode)
        jobs = [self._job(row) for row in conn.execute("SELECT * FROM ue_proxy_jobs WHERE episode_id=? ORDER BY created_at DESC LIMIT 5", (episode["id"],))]
        base = {"episode": episode, "ready": ue["ready"], "blockers": ue["blockers"], "shots": [], "jobs": jobs, "ffmpeg_configured": self.media.available(),
                "unreal_configured": ue["unreal_configured"], "summary": {"shots": 0, "with_proxy": 0, "locked": 0}}
        if not ue["ready"]:
            return base
        root = Path(self.studio.project(episode["project_id"])["root"]).resolve()
        width, height = proxy_size(conn, episode["project_id"])
        cache = {}
        for row in conn.execute("SELECT * FROM ue_cache_states WHERE episode_id=?", (episode["id"],)):
            cache.setdefault(row["shot_id"], {})[row["subsystem"]] = row
        stored = {row["shot_id"]: row["c6_hash"] for row in conn.execute("SELECT shot_id,c6_hash FROM ue_shot_states WHERE episode_id=?", (episode["id"],))}
        shots = []
        for shot in ue["shots"]:
            build = shot["build_hash"]
            declared = {sub: ({"status": row["status"], "note": row["note"], "current": row["build_hash"] == build} if (row := cache.get(shot["shot_id"], {}).get(sub)) else None)
                        for sub in SUBSYSTEMS}
            cache_ready = all(item and item["current"] for item in declared.values())
            row = conn.execute("SELECT * FROM ue_proxies WHERE episode_id=? AND shot_id=? ORDER BY version DESC LIMIT 1", (episode["id"], shot["shot_id"])).fetchone()
            proxy = None
            if row:
                proxy = {"id": row["id"], "version": row["version"], "provenance": row["provenance"], "frames": row["frames"], "width": row["width"], "height": row["height"],
                         "created_at": row["created_at"], "relative_path": row["relative_path"], "sha256": row["sha256"], "inspection": json.loads(row["inspection_json"]),
                         "file_ok": (root / row["relative_path"]).is_file(), "current": row["build_hash"] == build and row["frames"] >= shot["length_frames"]}
            issues, warnings = [], []
            if not shot["c5"]["locked"]:
                issues.append("Shot chưa chốt chuyển động ở C5.")
            elif not cache_ready:
                issues.append("Chưa khai báo cache tóc, vải và VFX cho đúng bản đã chốt C5.")
            seen = proxy["inspection"].get("cache_tracks") if proxy and proxy["current"] else None
            if seen:
                warnings += [f"Bạn khai báo đã bake cache {SUBSYSTEM_LABELS[sub]} nhưng Unreal không báo track cache tương ứng trong sequence; kiểm tra lại trong Sequencer."
                             for sub in SUBSYSTEMS if declared[sub] and declared[sub]["status"] == "BAKED" and not seen.get(sub)]
            usable = bool(proxy and proxy["current"] and proxy["file_ok"])
            c6_hash = digest({"build": build, "proxy": proxy["sha256"] if usable else None, "cache": {sub: declared[sub]["status"] if declared[sub] else None for sub in SUBSYSTEMS}})
            locked = shot["route"] == "UE" and bool(stored.get(shot["shot_id"])) and stored[shot["shot_id"]] == c6_hash and usable and cache_ready and shot["c5"]["locked"]
            shots.append({"shot_id": shot["shot_id"], "route": shot["route"], "description": shot["description"], "length_frames": shot["length_frames"], "seconds": shot["seconds"],
                          "c5_locked": shot["c5"]["locked"], "build_hash": build, "cache": declared, "cache_ready": cache_ready, "proxy": proxy, "proxy_usable": usable,
                          "issues": issues, "warnings": warnings, "c6_hash": c6_hash,
                          "c6": {"locked": locked, "stale": shot["route"] == "UE" and bool(stored.get(shot["shot_id"])) and not locked} if shot["route"] == "UE" else None,
                          "_item": shot["_item"]})
        summary = {"shots": len(shots), "with_proxy": sum(s["proxy_usable"] for s in shots), "locked": sum(bool(s["c6"] and s["c6"]["locked"]) for s in shots)}
        return {**base, "shots": shots, "summary": summary, "proxy_size": {"width": width, "height": height}, "_ue": ue,
                "cache_note": "Bake/cache tóc, vải và VFX thực hiện trong Unreal. App ghi lại khai báo của bạn và những track cache Unreal báo về; app không tự bake.",
                "final_note": "Proxy chỉ dùng cho rough cut. Chưa render final ở bước này."}

    @staticmethod
    def _public(state: dict) -> dict:
        return {**{k: v for k, v in state.items() if not k.startswith("_")},
                "shots": [{k: v for k, v in shot.items() if not k.startswith("_")} for shot in state["shots"]]}

    def state(self, episode_id: str) -> dict:
        with self.db.connect() as conn:
            return self._public(self._state(conn, self.breakdown._episode(conn, episode_id)))

    def _shot(self, conn, episode_id: str, shot_id: str) -> tuple[dict, dict, dict]:
        episode = self.breakdown._episode(conn, episode_id)
        state = self._state(conn, episode)
        if not state["ready"]:
            raise DomainError("Chưa làm việc được với proxy: " + " ".join(state["blockers"]), 409)
        return episode, state, require(next((s for s in state["shots"] if s["shot_id"] == shot_id), None), "shot")

    # ---- cache declarations ----------------------------------------------------------------------------------------

    def declare_cache(self, episode_id: str, shot_id: str, expected_build_hash: str, declarations: dict) -> dict:
        if not isinstance(declarations, dict) or set(declarations) != set(SUBSYSTEMS) or any(
                not isinstance(item, dict) or item.get("status") not in ("BAKED", "NOT_APPLICABLE") or not isinstance(item.get("note", ""), str) or len(item.get("note", "")) > 200
                for item in declarations.values()):
            raise DomainError("Khai báo đủ ba mục tóc, vải và VFX: đã bake cache hoặc không áp dụng; ghi chú tối đa 200 ký tự.")
        with self.db.transaction() as conn:
            episode, _, shot = self._shot(conn, episode_id, shot_id)
            if shot["build_hash"] != expected_build_hash or not shot["c5_locked"]:
                raise DomainError("Shot chưa chốt C5 hoặc đã đổi sau khi bạn xem; tải lại.", 409)
            for sub, item in declarations.items():
                conn.execute("INSERT OR REPLACE INTO ue_cache_states(episode_id,shot_id,project_id,subsystem,status,note,build_hash,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                             (episode_id, shot_id, episode["project_id"], sub, item["status"], item.get("note", "").strip(), expected_build_hash, now()))
            self.studio.audit(conn, episode["project_id"], "proxy.cache_declared", episode_id, {"shot": shot_id, **{sub: item["status"] for sub, item in declarations.items()}})
        return self.state(episode_id)

    # ---- proxy render ----------------------------------------------------------------------------------------------

    @staticmethod
    def _job(row) -> dict:
        item = {key: row[key] for key in ("id", "state", "stage", "error", "created_at", "updated_at")}
        item["shots"] = [shot["shot_id"] for shot in json.loads(row["request_json"])["shots"]]
        item["failed"] = json.loads(row["result_json"])["failed"] if row["result_json"] else []
        return item

    def _request(self, conn, state: dict, shot_ids: list[str] | None) -> dict:
        episode = state["episode"]
        chosen = []
        for shot in state["shots"]:
            if shot_ids is not None and shot["shot_id"] not in shot_ids:
                continue
            if shot["issues"]:
                if shot_ids is not None:
                    raise DomainError(f"{shot['shot_id']}: " + " ".join(shot["issues"]), 409)
                continue
            if shot_ids is None and (shot["proxy_usable"] or (shot["c6"] and shot["c6"]["locked"])):
                continue
            chosen.append(shot)
        if shot_ids is not None and {s["shot_id"] for s in chosen} != set(shot_ids):
            raise DomainError("Danh sách shot không hợp lệ hoặc không thuộc nhánh UE/Hybrid của tập.", 409)
        if not chosen:
            raise DomainError("Không có shot nào cần render proxy: cần chốt C5 và khai báo cache trước, shot đã có proxy đúng bản thì được giữ.", 409)
        width, height = proxy_size(conn, episode["project_id"])
        request = {"schema_version": "v45.ue-render-request.1", "quality": "PROXY", "image_format": "JPG", "temporal_samples": 1,
                   "project_id": episode["project_id"], "episode_code": episode["code"], "fps": state["_ue"]["_plan"]["fps"], "width": width, "height": height,
                   "shots": [{"shot_id": s["shot_id"], "sequence_path": s["_item"]["assemble"]["sequence_path"], "map_path": s["_item"]["assemble"]["map_path"],
                              "length_frames": s["length_frames"], "build_hash": s["build_hash"], "route": s["route"]} for s in chosen]}
        request["input_hash"] = digest(request)
        return request

    def enqueue_render(self, episode_id: str, shot_ids: list[str] | None = None) -> dict:
        self.studio._unreal_adapter()  # Fail before enqueue if Unreal is not configured.
        self.media.paths()  # Frames are turned into a clip with FFmpeg; fail before Unreal is started.
        with self.db.transaction() as conn:
            episode = self.breakdown._episode(conn, episode_id)
            state = self._state(conn, episode)
            if not state["ready"]:
                raise DomainError("Chưa render được proxy: " + " ".join(state["blockers"]), 409)
            active = conn.execute("SELECT state FROM ue_proxy_jobs WHERE episode_id=? AND state IN ('QUEUED','RUNNING','RECONCILING') LIMIT 1", (episode_id,)).fetchone()
            if active:
                raise DomainError("Lần render trước bị gián đoạn; đóng Unreal cũ và xác nhận trước khi render lại." if active["state"] == "RECONCILING" else "Đang có việc render proxy cho tập này.", 409)
            request = self._request(conn, state, shot_ids)
            request["operation_id"] = digest({"input_hash": request["input_hash"], "nonce": uid()})
            request["output_dir"] = str(self.db.data_root / "unreal-renders" / request["operation_id"] / "frames")
            conn.execute("INSERT INTO ue_proxy_jobs(id,project_id,episode_id,operation_id,input_hash,request_json,state,stage,result_json,error,created_at,updated_at) VALUES(?,?,?,?,?,?,'QUEUED','PREPARE',NULL,NULL,?,?)",
                         (uid(), episode["project_id"], episode_id, request["operation_id"], request["input_hash"], canonical(request), now(), now()))
            self.studio.audit(conn, episode["project_id"], "proxy.queued", episode_id, {"shots": len(request["shots"])})
        return self.state(episode_id)

    def _stage(self, job: dict, stage: str) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE ue_proxy_jobs SET stage=?,updated_at=? WHERE id=?", (stage, now(), job["id"]))

    def _register(self, conn, job_id: str | None, episode: dict, shot: dict, source: Path, provenance: str, inspection: dict) -> dict:
        """Copy or move a finished clip into the project as the next proxy version of the shot."""
        root = Path(self.studio.project(episode["project_id"])["root"]).resolve()
        version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM ue_proxies WHERE episode_id=? AND shot_id=?", (episode["id"], shot["shot_id"])).fetchone()[0]
        relative = f"media/proxy/{episode['code']}/{shot['shot_id']}/proxy-v{version}.mp4"
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise DomainError("Đã có file proxy cùng tên trong dự án; kiểm tra thư mục media/proxy.", 409)
        shutil.copyfile(source, target)
        info = self.media.probe_video(target)
        if info["frames"] < shot["length_frames"]:
            target.unlink()
            raise DomainError(f"Proxy có {info['frames']} frame, shot cần {shot['length_frames']} frame theo timing đã duyệt.", 409)
        item = {"id": uid(), "relative_path": relative, "sha256": file_sha256(target), "frames": info["frames"]}
        conn.execute("INSERT INTO ue_proxies(id,project_id,episode_id,shot_id,version,job_id,provenance,build_hash,relative_path,sha256,bytes,frames,width,height,inspection_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (item["id"], episode["project_id"], episode["id"], shot["shot_id"], version, job_id, provenance, shot["build_hash"], relative, item["sha256"], target.stat().st_size,
                      info["frames"], info["width"], info["height"], canonical(inspection), now()))
        return {**item, "path": target}

    def _hybrid_frame(self, episode_id: str, shot: dict, clip: Path) -> str | None:
        """A Hybrid shot's proxy is the UE source of its AI draft; its first frame is what the provider can take today."""
        if shot["route"] != "HYBRID":
            return None
        frame = clip.with_name(clip.stem + "-first.png")
        try:
            self.media.first_frame(clip, frame)
            self.ai.register_control(episode_id, shot["shot_id"], str(frame))
            return None
        except DomainError as exc:
            return f"Chưa đăng ký được frame đầu làm nguồn Hybrid: {exc}"
        finally:
            frame.unlink(missing_ok=True)

    def process_next(self) -> dict | None:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM ue_proxy_jobs WHERE state='QUEUED' ORDER BY created_at LIMIT 1").fetchone()
            if not row or gpu_queue.holder(conn):
                return None
            job = dict(row)
            gpu_queue.try_acquire(conn, job["operation_id"], "MRQ")
            conn.execute("UPDATE ue_proxy_jobs SET state='RUNNING',updated_at=? WHERE id=?", (now(), job["id"]))
        request = json.loads(job["request_json"])
        failed, done = [], 0
        try:
            with self.db.connect() as conn:
                episode = self.breakdown._episode(conn, job["episode_id"])
                current = self._request(conn, self._state(conn, episode), [s["shot_id"] for s in request["shots"]])
            if current["input_hash"] != job["input_hash"]:
                raise DomainError("Shot đã đổi sau khi xếp việc render; xếp lại.", 409)
            result = self.preparer(request)
            prepared = prepared_shots(result, request)
            failed = [{"shot_id": key, "error": str(item.get("error"))[:300]} for key, item in prepared.items() if item.get("status") != "OK"]
            if result.get("manifest"):
                self._stage(job, "RENDER")
                self.renderer(result["manifest"])
            self._stage(job, "ENCODE")
            work = Path(request["output_dir"])
            for shot in request["shots"]:
                if prepared[shot["shot_id"]].get("status") != "OK":
                    continue
                clip = work / (shot["shot_id"] + ".mp4")
                try:
                    self.media.encode_frames(work / shot["shot_id"], clip, request["fps"], shot["length_frames"])
                    with self.db.transaction() as conn:
                        self._register(conn, job["id"], episode, shot, clip, "UE_MRQ", prepared[shot["shot_id"]].get("inspection") or {})
                    done += 1
                    note = self._hybrid_frame(job["episode_id"], shot, clip)
                    if note:
                        failed.append({"shot_id": shot["shot_id"], "error": note})
                except (DomainError, OSError) as exc:
                    failed.append({"shot_id": shot["shot_id"], "error": str(exc)[:300]})
            shutil.rmtree(work, ignore_errors=True)
            with self.db.transaction() as conn:
                conn.execute("UPDATE ue_proxy_jobs SET state=?,result_json=?,error=?,updated_at=? WHERE id=?",
                             ("COMPLETED" if done else "BLOCKED", canonical({"failed": failed, "proxies": done}), None if done else "Không tạo được proxy nào; xem lỗi từng shot.", now(), job["id"]))
                gpu_queue.release(conn, job["operation_id"])
                self.studio.audit(conn, job["project_id"], "proxy.completed", job["id"], {"ok": done, "failed": len(failed)})
        except Exception as exc:
            # Both Unreal processes have ended; the render writes only into the app's own work folder.
            with self.db.transaction() as conn:
                conn.execute("UPDATE ue_proxy_jobs SET state='BLOCKED',result_json=?,error=?,updated_at=? WHERE id=?",
                             (canonical({"failed": failed, "proxies": done}), str(exc)[:500] if isinstance(exc, DomainError) else f"{type(exc).__name__}: không render được proxy.", now(), job["id"]))
                gpu_queue.release(conn, job["operation_id"])
        with self.db.connect() as conn:
            return self._job(conn.execute("SELECT * FROM ue_proxy_jobs WHERE id=?", (job["id"],)).fetchone())

    def release_interrupted(self, job_id: str, worker_stopped: bool) -> dict:
        with self.db.transaction() as conn:
            job = require(conn.execute("SELECT * FROM ue_proxy_jobs WHERE id=?", (job_id,)).fetchone(), "proxy job")
            if job["state"] != "RECONCILING":
                raise DomainError("Việc render này không ở trạng thái cần xác nhận.", 409)
            if not worker_stopped:
                raise DomainError("Đóng cửa sổ Unreal của lần render cũ rồi xác nhận trong giao diện.", 409)
            conn.execute("UPDATE ue_proxy_jobs SET state='BLOCKED',error='Bị gián đoạn; render lại khi cần.',updated_at=? WHERE id=?", (now(), job_id))
            gpu_queue.release(conn, job["operation_id"])
        return self.state(job["episode_id"])

    def import_proxy(self, episode_id: str, shot_id: str, source_path: str) -> dict:
        """A clip the operator rendered from Unreal by hand stands in for the app's own proxy render."""
        source = Path(source_path)
        if not source.is_file():
            raise DomainError("Không tìm thấy file đã chọn.")
        with self.db.transaction() as conn:
            episode, _, shot = self._shot(conn, episode_id, shot_id)
            if shot["issues"]:
                raise DomainError(f"{shot_id}: " + " ".join(shot["issues"]), 409)
            item = self._register(conn, None, episode, shot, source, "OPERATOR_FILE", {})
            self.studio.audit(conn, episode["project_id"], "proxy.imported", episode_id, {"shot": shot_id, "frames": item["frames"]})
        note = self._hybrid_frame(episode_id, shot, item["path"])
        return {**self.state(episode_id), "import_note": note}

    def proxy_file(self, proxy_id: str) -> Path:
        with self.db.connect() as conn:
            row = require(conn.execute("SELECT * FROM ue_proxies WHERE id=?", (proxy_id,)).fetchone(), "proxy")
        path = Path(self.studio.project(row["project_id"])["root"]).resolve() / row["relative_path"]
        if not path.is_file() or path.stat().st_size != row["bytes"]:
            raise DomainError("File proxy thiếu hoặc đã bị đổi ngoài app.", 409)
        return path

    # ---- C6 --------------------------------------------------------------------------------------------------------

    def approve_c6(self, episode_id: str, shot_id: str, expected_hash: str) -> dict:
        """The cache has been checked and this proxy is the one the rough cut uses for the shot."""
        with self.db.transaction() as conn:
            episode, _, shot = self._shot(conn, episode_id, shot_id)
            if shot["route"] != "UE":
                raise DomainError("Shot Hybrid chốt C6 bằng draft AI ở nhánh AI.", 409)
            if shot["c6_hash"] != expected_hash:
                raise DomainError("Cache hoặc proxy đã đổi sau khi bạn xem; tải lại.", 409)
            if shot["issues"] or not shot["proxy_usable"]:
                raise DomainError("Chưa chốt được C6: " + (" ".join(shot["issues"]) or "chưa có proxy đúng với bản đã chốt C5."), 409)
            if file_sha256(self.proxy_file(shot["proxy"]["id"])) != shot["proxy"]["sha256"]:
                raise DomainError("File proxy đã bị đổi ngoài app.", 409)
            if shot["c6"]["locked"]:
                raise DomainError("Shot này đã chốt C6.", 409)
            sequence = conn.execute("SELECT COALESCE(MAX(exact_version),0)+1 FROM workflow_checkpoints WHERE project_id=? AND checkpoint='C6' AND scope_id=?", (episode["project_id"], shot_id)).fetchone()[0]
            workflow.record_checkpoint(self.studio, conn, episode["project_id"], "C6", shot_id, sequence, expected_hash,
                                       {"branch": "UE", "episode_id": episode_id, "proxy_id": shot["proxy"]["id"], "proxy_version": shot["proxy"]["version"],
                                        "provenance": shot["proxy"]["provenance"], "cache": {sub: shot["cache"][sub]["status"] for sub in SUBSYSTEMS},
                                        "cache_tracks_reported": shot["proxy"]["inspection"].get("cache_tracks"), "final_rendered": False})
            conn.execute("UPDATE ue_shot_states SET c6_hash=?,updated_at=? WHERE episode_id=? AND shot_id=?", (expected_hash, now(), episode_id, shot_id))
        return self.state(episode_id)

    def unlock_c6(self, episode_id: str, shot_id: str) -> dict:
        with self.db.transaction() as conn:
            episode = self.breakdown._episode(conn, episode_id)
            if not conn.execute("UPDATE ue_shot_states SET c6_hash=NULL,updated_at=? WHERE episode_id=? AND shot_id=? AND c6_hash IS NOT NULL", (now(), episode_id, shot_id)).rowcount:
                raise DomainError("Shot này chưa chốt C6.", 409)
            self.studio.audit(conn, episode["project_id"], "proxy.c6_unlocked", episode_id, {"shot": shot_id})
        return self.state(episode_id)
