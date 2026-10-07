"""Workflow V4.5 zone C: finals (step 10), conform (step 11) and the technical review of finals (C8).

A final is made only for a shot whose scene is under Picture Lock. UE shots are rendered with the Movie
Render Queue; AI and Hybrid shots are generated again by the provider from the draft that C6 fixed. Every
final is then conformed to the series standard. A final that fails review is made again for that shot
alone; Picture Lock is not reopened.
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import budget, gpu_queue, workflow
from .ai_video_provider import VideoNotSent, VideoOutcomeUnknown
from .domain import DomainError, canonical, digest, file_sha256
from .media_tools import COLOR_TAGS, MASTER_LABEL, MediaTools
from .proxy_service import prepared_shots, proxy_size
from .route_domain import MODEL_LABELS
from .service import now, require, uid


POLL_SECONDS = 10
FINAL_TEMPORAL_SAMPLES = 8
CHECKS = ("match", "artifacts")
AI_CHECKS = (*CHECKS, "watermark")
VIDEO_SUFFIXES = (".mp4", ".mov")


class FinalService:
    def __init__(self, studio, breakdown, proxy, ai, rough, *, preparer=None, renderer=None, media=None):
        self.studio = studio
        self.db = studio.db
        self.breakdown = breakdown
        self.proxy = proxy
        self.ai = ai
        self.rough = rough
        self.preparer = preparer or (lambda request: studio._unreal_adapter().prepare_render(request))
        self.renderer = renderer or (lambda manifest: studio._unreal_adapter().render_queue(manifest))
        self.media = media or MediaTools(studio)
        with self.db.transaction() as conn:
            # A paid submit interrupted by a restart may or may not have created a task; a render may still be running.
            conn.execute("UPDATE final_jobs SET state='RECONCILING',error=CASE WHEN kind='AI_FINAL' THEN 'Backend khởi động lại trong lúc gửi yêu cầu final.' "
                         "ELSE 'Backend khởi động lại trong lúc Unreal đang render final; đóng Unreal cũ rồi xác nhận.' END,updated_at=? WHERE state='RUNNING'", (now(),))

    # ---- state -----------------------------------------------------------------------------------------------------

    @staticmethod
    def _standards(conn, project_id: str) -> dict:
        row = require(conn.execute("SELECT fps_num,fps_den,width,height,color_space FROM series_standards WHERE project_id=?", (project_id,)).fetchone(), "series standards")
        return {"fps": {"num": row["fps_num"], "den": row["fps_den"]}, "width": row["width"], "height": row["height"], "color_space": row["color_space"]}

    def _state(self, conn, episode: dict) -> dict:
        rough = self.rough._state(conn, episode)
        jobs = [self._job(row) for row in conn.execute("SELECT * FROM final_jobs WHERE episode_id=? AND kind='UE_RENDER' ORDER BY created_at DESC LIMIT 3", (episode["id"],))]
        base = {"episode": episode, "ready": rough["ready"], "blockers": rough["blockers"], "shots": [], "jobs": jobs, "ffmpeg_configured": self.media.available(),
                "summary": {"shots": 0, "locked": 0, "with_final": 0, "conformed": 0, "approved": 0}, "gate3": {"shots": 0, "totals": [], "allowed": False}}
        if not rough["ready"]:
            return base
        plan, root = rough["_plan"], rough["_root"]
        standards = self._standards(conn, episode["project_id"])
        configs = {}
        takes = {row["id"]: row for row in conn.execute("SELECT * FROM ai_video_takes WHERE episode_id=?", (episode["id"],))}
        approvals = {row["shot_id"]: row for row in conn.execute("SELECT * FROM final_approvals WHERE episode_id=?", (episode["id"],))}
        active = {row["shot_id"]: row for row in conn.execute("SELECT * FROM final_jobs WHERE episode_id=? AND kind='AI_FINAL' ORDER BY created_at", (episode["id"],))}
        shots, sums = [], {}
        for scene in rough["scenes"]:
            locked = bool(scene["lock"] and scene["lock"]["locked"])
            for cut in scene["shots"]:
                shot = plan["by_id"][cut["shot_id"]]
                length = shot["length_frames"]
                issues, basis, source, estimate, view = [], None, None, None, {}
                if not locked:
                    issues.append("Scene của shot chưa khóa dựng (C7).")
                if shot["route"] == "UE":
                    item = rough["_ue"][shot["shot_id"]]
                    source = {"kind": "UE", "label": "Render final bằng Movie Render Queue từ cache đã kiểm tra"}
                    if item["c6"]["locked"]:
                        basis = digest({"c6": item["c6_hash"], "frames": length})
                    view = {"sequence_path": item["_item"]["assemble"]["sequence_path"], "map_path": item["_item"]["assemble"]["map_path"]}
                else:
                    item = rough["_ai"][shot["shot_id"]]
                    draft = next((t for t in item["takes"] if t["locked"]), None) if item["c6"]["locked"] else None
                    if draft:
                        take = takes[draft["id"]]
                        model = take["model"]
                        config = configs.setdefault(model, self.ai.settings.one(model))
                        provider = self.ai.provider_factory(model, config)
                        price = plan["prices"]["video"][model]
                        basis = digest({"draft": take["id"], "sha256": take["sha256"], "frames": length})
                        estimate = {"micro": take["requested_seconds"] * price["final_micro_per_second"], "currency": price["currency"]}
                        source = {"kind": "AI_DRAFT", "label": f"Final {provider.final_resolution} từ draft bản {take['version']} · {MODEL_LABELS[model]}",
                                  "expires_in_hours": draft["expires_in_hours"], "expired": draft["expired"], "expiring": draft["expiring"]}
                        view = {"model": model, "model_id": config["model_id"], "platform": config["platform"], "draft_task_id": take["provider_task_id"], "draft_take_id": take["id"],
                                "seconds": take["requested_seconds"], "provider_kind": provider.kind, "_config": config}
                        if draft["expired"]:
                            # Reached only when the draft already has a final: asking the provider again is what the window closes.
                            issues.append("Draft đã quá hạn dùng để tạo thêm final. Final đã có vẫn dùng được; muốn final khác thì nhập final làm tay, hoặc tạo và chốt draft mới ở tab Dựng shot rồi khóa dựng lại scene.")
                        elif not provider.supports_final_from_draft:
                            issues.append(f"App chưa có đặc tả API tạo final từ draft của {MODEL_LABELS[model]}; tạo final trên trang nhà cung cấp rồi dùng “Nhập final làm tay”.")
                        elif not config["configured"]:
                            issues.append(f"{MODEL_LABELS[model]} chưa được cấu hình đủ nền tảng, model ID và khóa ở Cài đặt → Model video AI.")
                        elif not price["final_micro_per_second"]:
                            issues.append(f"Chưa có giá final của {MODEL_LABELS[model]} trong bảng giá ở C3.")
                    else:
                        source = {"kind": "AI_DRAFT", "label": "Chưa có draft đã chốt C6"}
                spec = digest({**standards, "frames": length})
                row = conn.execute("SELECT * FROM final_takes WHERE episode_id=? AND shot_id=? ORDER BY version DESC LIMIT 1", (episode["id"], shot["shot_id"])).fetchone()
                final = None
                if row:
                    final = {"id": row["id"], "version": row["version"], "provenance": row["provenance"], "frames": row["frames"], "width": row["width"], "height": row["height"],
                             "provider_task_id": row["provider_task_id"], "created_at": row["created_at"], "current": bool(basis) and row["basis_hash"] == basis,
                             "file_ok": (root / row["relative_path"]).is_file()}
                done = conn.execute("SELECT * FROM conformed_shots WHERE final_take_id=? ORDER BY version DESC LIMIT 1", (row["id"],)).fetchone() if row else None
                conform = None
                if done:
                    conform = {"id": done["id"], "version": done["version"], "sha256": done["sha256"], "relative_path": done["relative_path"], "qc": json.loads(done["qc_json"]),
                               "created_at": done["created_at"], "file_ok": (root / done["relative_path"]).is_file() and (root / done["review_path"]).is_file(),
                               "current": final["current"] and done["spec_hash"] == spec}
                usable = bool(conform and conform["current"] and conform["file_ok"] and conform["qc"]["passed"])
                c8_hash = digest({"conform": conform["sha256"], "basis": basis}) if usable else None
                approval = approvals.get(shot["shot_id"])
                approved = bool(approval) and usable and locked and approval["c8_hash"] == c8_hash
                job = active.get(shot["shot_id"])
                waiting = locked and not issues and bool(basis) and not (final and final["current"]) and not (job and job["state"] in ("QUEUED", "RUNNING", "PENDING", "RECONCILING"))
                if waiting and estimate:
                    sums[estimate["currency"]] = sums.get(estimate["currency"], 0) + estimate["micro"]
                segment = self.rough._segment(root, episode["code"], cut, rough["fps"], rough["size"]) if cut["source"]["ready"] else None
                shots.append({"shot_id": shot["shot_id"], "scene_key": shot["scene_key"], "route": shot["route"], "description": shot["description"], "length_frames": length,
                              "seconds": shot["seconds"], "picture_locked": locked, "source": source, "estimate": estimate, "issues": issues, "final": final, "conform": conform,
                              "job": {k: job[k] for k in ("id", "state", "stage", "error", "provider_task_id")} if job else None,
                              "c8": {"approved": approved, "stale": bool(approval) and not approved}, "c8_hash": c8_hash, "checks": list(CHECKS if shot["route"] == "UE" else AI_CHECKS),
                              "has_rough_segment": bool(segment and segment.is_file()), "needs_final": waiting,
                              "_basis": basis, "_spec": spec, "_view": view})
        totals = [{"currency": currency, "micro": amount, "budget": budget.check(conn, episode["project_id"], currency, amount)} for currency, amount in sorted(sums.items())]
        ai_waiting = sum(s["needs_final"] and s["route"] != "UE" for s in shots)
        summary = {"shots": len(shots), "locked": sum(s["picture_locked"] for s in shots), "with_final": sum(bool(s["final"] and s["final"]["current"]) for s in shots),
                   "conformed": sum(bool(s["conform"] and s["conform"]["current"] and s["conform"]["qc"]["passed"]) for s in shots), "approved": sum(s["c8"]["approved"] for s in shots),
                   "ue_waiting": sum(s["needs_final"] and s["route"] == "UE" for s in shots)}
        return {**base, "shots": shots, "summary": summary, "standards": standards, "master_label": MASTER_LABEL, "unreal_configured": rough["_unreal_configured"],
                "color_supported": standards["color_space"] in COLOR_TAGS,
                "gate3": {"shots": ai_waiting, "totals": totals, "allowed": ai_waiting > 0 and all(t["budget"]["allowed"] for t in totals)},
                "notes": {"final": "Final AI được suy luận lại từ draft với cùng prompt, tham chiếu và seed: giữ bố cục và chuyển động, không giống draft từng pixel.",
                          "watermark": "App không tự phát hiện watermark; bạn xem bản final rồi đánh dấu.",
                          "retry": "Final lỗi thì tạo lại riêng shot đó; khóa dựng không bị mở."},
                "_root": root, "_rough": rough}

    @staticmethod
    def _public(state: dict) -> dict:
        return {**{k: v for k, v in state.items() if not k.startswith("_")},
                "shots": [{k: v for k, v in shot.items() if not k.startswith("_")} for shot in state["shots"]]}

    def state(self, episode_id: str) -> dict:
        with self.db.connect() as conn:
            return self._public(self._state(conn, self.breakdown._episode(conn, episode_id)))

    def _ready(self, conn, episode_id: str) -> tuple[dict, dict]:
        episode = self.breakdown._episode(conn, episode_id)
        state = self._state(conn, episode)
        if not state["ready"]:
            raise DomainError("Chưa tạo được final: " + " ".join(state["blockers"]), 409)
        return episode, state

    @staticmethod
    def _job(row) -> dict:
        item = {key: row[key] for key in ("id", "kind", "shot_id", "state", "stage", "error", "provider_task_id", "estimated_micro", "actual_micro", "currency", "updated_at")}
        item["shots"] = [shot["shot_id"] for shot in json.loads(row["request_json"]).get("shots", [])] or ([row["shot_id"]] if row["shot_id"] else [])
        item["failed"] = json.loads(row["result_json"]).get("failed", []) if row["result_json"] else []
        return item

    # ---- step 11: conform ------------------------------------------------------------------------------------------

    def _register(self, conn, episode: dict, shot: dict, source: Path, provenance: str, job_id: str | None, task_id: str | None) -> dict:
        """Copy what step 10 produced into the project as the next final of the shot."""
        info = self.media.probe_media(source)
        root = Path(self.studio.project(episode["project_id"])["root"]).resolve()
        version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM final_takes WHERE episode_id=? AND shot_id=?", (episode["id"], shot["shot_id"])).fetchone()[0]
        relative = f"media/final/{episode['code']}/{shot['shot_id']}/final-v{version}{source.suffix.lower()}"
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise DomainError("Đã có file final cùng tên trong dự án; kiểm tra thư mục media/final.", 409)
        shutil.copyfile(source, target)
        item = {"id": uid(), "project_id": episode["project_id"], "episode_id": episode["id"], "shot_id": shot["shot_id"], "version": version, "job_id": job_id,
                "provenance": provenance, "route": shot["route"], "model": shot["_view"].get("model"), "provider_task_id": task_id, "draft_take_id": shot["_view"].get("draft_take_id"),
                "basis_hash": shot["_basis"],
                "relative_path": relative, "sha256": file_sha256(target), "bytes": target.stat().st_size, "frames": info["frames"], "width": info["width"], "height": info["height"],
                "fps_num": info["fps_num"], "fps_den": info["fps_den"], "audio_streams": info["audio_streams"], "created_at": now()}
        conn.execute("INSERT INTO final_takes(id,project_id,episode_id,shot_id,version,job_id,provenance,route,model,provider_task_id,draft_take_id,basis_hash,relative_path,sha256,bytes,frames,width,height,fps_num,fps_den,audio_streams,created_at) "
                     "VALUES(:id,:project_id,:episode_id,:shot_id,:version,:job_id,:provenance,:route,:model,:provider_task_id,:draft_take_id,:basis_hash,:relative_path,:sha256,:bytes,:frames,:width,:height,:fps_num,:fps_den,:audio_streams,:created_at)", item)
        return item

    def _conform(self, episode: dict, take: dict, length: int, spec: str) -> dict:
        """Bring one final to the series standard and check the result. Runs FFmpeg, so it is called outside any transaction."""
        with self.db.connect() as conn:
            standards = self._standards(conn, episode["project_id"])
            version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM conformed_shots WHERE episode_id=? AND shot_id=?", (episode["id"], take["shot_id"])).fetchone()[0]
            width, height = proxy_size(conn, episode["project_id"])
        root = Path(self.studio.project(episode["project_id"])["root"]).resolve()
        folder = f"media/final/{episode['code']}/{take['shot_id']}"
        master, review = root / f"{folder}/conformed-v{version}.mov", root / f"{folder}/review-v{version}.mp4"
        if master.exists() or review.exists():
            raise DomainError("Đã có file chuẩn hóa cùng tên trong dự án; kiểm tra thư mục media/final.", 409)
        source = root / take["relative_path"]
        if not source.is_file() or file_sha256(source) != take["sha256"]:
            raise DomainError("File final thiếu hoặc đã bị đổi ngoài app.", 409)
        self.media.conform_final(source, master, standards["fps"], length, standards["width"], standards["height"], standards["color_space"])
        info = self.media.probe_media(master)
        aspect = abs(take["width"] / take["height"] - standards["width"] / standards["height"]) < 0.01
        checks = [
            {"key": "frames", "label": "Đúng số frame của shot", "ok": info["frames"] == length, "detail": f"{info['frames']}/{length} frame" + ("" if info["frames"] == length else " · nguồn final ngắn hơn shot")},
            {"key": "size", "label": "Đúng độ phân giải chuẩn", "ok": (info["width"], info["height"]) == (standards["width"], standards["height"]), "detail": f"{info['width']}×{info['height']} (nguồn {take['width']}×{take['height']})"},
            {"key": "fps", "label": "Đúng FPS chuẩn", "ok": (info["fps_num"], info["fps_den"]) == (standards["fps"]["num"], standards["fps"]["den"]), "detail": f"{info['fps_num']}/{info['fps_den']} (nguồn {take['fps_num']}/{take['fps_den']})"},
            {"key": "audio", "label": "Đã bỏ audio tự sinh", "ok": info["audio_streams"] == 0, "detail": f"nguồn có {take['audio_streams']} luồng audio"},
            {"key": "aspect", "label": "Cùng tỉ lệ khung hình với chuẩn", "ok": aspect, "detail": "khớp" if aspect else "khác tỉ lệ: đã thêm viền đen để giữ nguyên hình", "warning_only": True},
        ]
        self.media.conform(master, review, standards["fps"], length, width, height)
        qc = {"checks": checks, "passed": all(c["ok"] for c in checks if not c.get("warning_only")), "codec": info["codec"], "pix_fmt": info["pix_fmt"], "color_space": standards["color_space"]}
        item = {"id": uid(), "relative_path": f"{folder}/conformed-v{version}.mov", "sha256": file_sha256(master), "qc": qc}
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO conformed_shots(id,project_id,episode_id,shot_id,version,final_take_id,spec_hash,relative_path,sha256,bytes,review_path,qc_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (item["id"], episode["project_id"], episode["id"], take["shot_id"], version, take["id"], spec, item["relative_path"], item["sha256"], master.stat().st_size,
                          f"{folder}/review-v{version}.mp4", canonical(qc), now()))
            self.studio.audit(conn, episode["project_id"], "final.conformed", episode["id"], {"shot": take["shot_id"], "passed": qc["passed"]})
        return item

    def _conform_safely(self, episode: dict, take: dict, shot: dict) -> str | None:
        try:
            self._conform(episode, take, shot["length_frames"], shot["_spec"])
            return None
        except (DomainError, OSError) as exc:
            return "Đã có final nhưng chưa chuẩn hóa được: " + str(exc)[:300]

    def reconform(self, episode_id: str, shot_id: str) -> dict:
        """Conform the current final again, for example after the series standard changed."""
        with self.db.connect() as conn:
            episode, state = self._ready(conn, episode_id)
            shot = require(next((s for s in state["shots"] if s["shot_id"] == shot_id), None), "shot")
            if not shot["final"] or not shot["final"]["current"]:
                raise DomainError("Shot chưa có final đúng với bản đã khóa dựng.", 409)
            take = dict(conn.execute("SELECT * FROM final_takes WHERE id=?", (shot["final"]["id"],)).fetchone())
        self._conform(episode, take, shot["length_frames"], shot["_spec"])
        return self.state(episode_id)

    def import_final(self, episode_id: str, shot_id: str, source_path: str) -> dict:
        """A final made outside the app (provider's own site, a hand render) goes through the same conform and review."""
        source = Path(source_path)
        if not source.is_file() or source.suffix.lower() not in VIDEO_SUFFIXES:
            raise DomainError("Chọn file video MP4 hoặc MOV có trên máy này.")
        self.media.paths()
        with self.db.transaction() as conn:
            episode, state = self._ready(conn, episode_id)
            shot = require(next((s for s in state["shots"] if s["shot_id"] == shot_id), None), "shot")
            if not shot["picture_locked"] or not shot["_basis"]:
                raise DomainError("Chỉ shot thuộc scene đã khóa dựng mới nhận final.", 409)
            take = self._register(conn, episode, shot, source, "OPERATOR_FILE", None, None)
            self.studio.audit(conn, episode["project_id"], "final.imported", episode_id, {"shot": shot_id})
        note = self._conform_safely(episode, take, shot)
        return {**self.state(episode_id), "import_note": note}

    # ---- step 10: AI finals through budget gate 3 ------------------------------------------------------------------

    def enqueue_ai(self, episode_id: str, shot_ids: list[str] | None, client_request_id: str) -> dict:
        """Send finals for every waiting AI or Hybrid shot, or make the named shots again. All of them pass gate 3 together."""
        try:
            uuid.UUID(client_request_id)
        except (ValueError, AttributeError) as exc:
            raise DomainError("client_request_id phải là UUID.") from exc
        self.media.paths()  # A paid final that cannot be conformed afterwards is of no use.
        queued = 0
        with self.db.transaction() as conn:
            episode, state = self._ready(conn, episode_id)
            if not state["color_supported"]:
                raise DomainError(f"Chuẩn màu {state['standards']['color_space']} của series chưa chuẩn hóa được trong app; đổi chuẩn ở S1 hoặc xử lý màu ngoài app.", 409)
            pool = [s for s in state["shots"] if s["route"] != "UE"]
            if shot_ids is None:
                targets = [s for s in pool if s["needs_final"]]
            else:
                targets = [s for s in pool if s["shot_id"] in shot_ids]
                if not shot_ids or len(targets) != len(set(shot_ids)):
                    raise DomainError("Danh sách shot không hợp lệ hoặc không thuộc nhánh AI/Hybrid.", 409)
                for shot in targets:
                    if shot["issues"] or not shot["_basis"]:
                        raise DomainError(f"{shot['shot_id']}: " + (" ".join(shot["issues"]) or "chưa có draft đã chốt C6."), 409)
                    if shot["job"] and shot["job"]["state"] in ("QUEUED", "RUNNING", "PENDING", "RECONCILING"):
                        raise DomainError(f"{shot['shot_id']}: " + ("lần tạo final trước chưa rõ kết quả; đóng khoản giữ trong Sổ chi phí trước khi gửi lại." if shot["job"]["state"] == "RECONCILING" else "đang có yêu cầu final."), 409)
            if not targets:
                raise DomainError("Không có shot AI/Hybrid nào đang chờ final: cần scene đã khóa dựng và draft đã chốt còn hạn.", 409)
            for shot in targets:
                view = shot["_view"]
                request = {"schema_version": "ai-final.request.1", "episode_id": episode_id, "shot_id": shot["shot_id"], "stage": "FINAL", "route": shot["route"],
                           "model": view["model"], "model_id": view["model_id"], "platform": view["platform"], "draft_task_id": view["draft_task_id"],
                           "draft_take_id": view["draft_take_id"], "seconds": view["seconds"], "basis_hash": shot["_basis"]}
                input_hash = digest(request)
                operation_id = digest({"input_hash": input_hash, "client_request_id": client_request_id})
                if conn.execute("SELECT 1 FROM final_jobs WHERE operation_id=?", (operation_id,)).fetchone():
                    continue
                budget.reserve(conn, episode["project_id"], operation_id, gate=3, category="VIDEO_FINAL", provider_kind=view["provider_kind"],
                               currency=shot["estimate"]["currency"], amount_micro=shot["estimate"]["micro"])
                request["operation_id"] = operation_id
                conn.execute("INSERT INTO final_jobs(id,project_id,episode_id,kind,shot_id,provider_kind,model,operation_id,input_hash,request_json,state,stage,provider_task_id,estimated_micro,actual_micro,currency,error,result_json,poll_after,created_at,updated_at) VALUES(?,?,?,'AI_FINAL',?,?,?,?,?,?,'QUEUED','SUBMIT',NULL,?,NULL,?,NULL,NULL,NULL,?,?)",
                             (uid(), episode["project_id"], episode_id, shot["shot_id"], view["provider_kind"], view["model"], operation_id, input_hash, canonical(request),
                              shot["estimate"]["micro"], shot["estimate"]["currency"], now(), now()))
                queued += 1
            self.studio.audit(conn, episode["project_id"], "final.ai_queued", episode_id, {"shots": queued})
        return {**self.state(episode_id), "queued": queued}

    def _finish(self, job: dict, state: str, error: str | None, *, billed: bool = False, release: bool = False, result: dict | None = None) -> dict:
        with self.db.transaction() as conn:
            conn.execute("UPDATE final_jobs SET state=?,error=?,actual_micro=CASE WHEN ? THEN estimated_micro ELSE actual_micro END,result_json=COALESCE(?,result_json),updated_at=? WHERE id=?",
                         (state, error, int(billed), canonical(result) if result is not None else None, now(), job["id"]))
            if billed:
                budget.settle(conn, job["operation_id"], job["estimated_micro"])
            elif release:
                budget.release(conn, job["operation_id"])
            self.studio.audit(conn, job["project_id"], "final." + state.lower(), job["id"], {"kind": job["kind"], "shot": job["shot_id"]})
            return self._job(conn.execute("SELECT * FROM final_jobs WHERE id=?", (job["id"],)).fetchone())

    def _current_shot(self, job: dict, request: dict) -> tuple[dict, dict]:
        with self.db.connect() as conn:
            episode, state = self._ready(conn, job["episode_id"])
        shot = next((s for s in state["shots"] if s["shot_id"] == request["shot_id"]), None)
        if not shot or not shot["picture_locked"] or shot["_basis"] != request["basis_hash"] or shot["issues"]:
            raise VideoNotSent("Khóa dựng, draft hoặc cấu hình đã đổi sau khi xếp việc; yêu cầu final chưa gửi.", 409)
        return episode, shot

    def process_next_ai(self) -> dict | None:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM final_jobs WHERE state='QUEUED' AND kind='AI_FINAL' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            job = dict(row)
            conn.execute("UPDATE final_jobs SET state='RUNNING',updated_at=? WHERE id=?", (now(), job["id"]))
        request = json.loads(job["request_json"])
        try:
            _, shot = self._current_shot(job, request)
            task_id = self.ai.provider_factory(job["model"], shot["_view"]["_config"]).submit_final(request)
        except VideoNotSent as exc:
            return self._finish(job, "BLOCKED", str(exc)[:500], release=True)
        except VideoOutcomeUnknown as exc:
            return self._finish(job, "RECONCILING", str(exc)[:500])
        except DomainError as exc:
            return self._finish(job, "BLOCKED", str(exc)[:500], release=True)
        except Exception as exc:
            return self._finish(job, "RECONCILING", f"{type(exc).__name__}: không rõ yêu cầu final đã tới nhà cung cấp hay chưa.")
        with self.db.transaction() as conn:
            later = (datetime.now(timezone.utc) + timedelta(seconds=POLL_SECONDS)).isoformat()
            conn.execute("UPDATE final_jobs SET state='PENDING',stage='WAIT',provider_task_id=?,poll_after=?,updated_at=? WHERE id=?", (task_id, later, now(), job["id"]))
            return self._job(conn.execute("SELECT * FROM final_jobs WHERE id=?", (job["id"],)).fetchone())

    def poll_next(self) -> dict | None:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM final_jobs WHERE state='PENDING' AND poll_after<=? ORDER BY poll_after LIMIT 1", (now(),)).fetchone()
            if not row:
                return None
            job = dict(row)
            later = (datetime.now(timezone.utc) + timedelta(seconds=POLL_SECONDS)).isoformat()
            conn.execute("UPDATE final_jobs SET poll_after=?,updated_at=? WHERE id=?", (later, now(), job["id"]))
        request = json.loads(job["request_json"])
        try:
            provider = self.ai.provider_factory(job["model"], self.ai.settings.one(job["model"]))
            answer = provider.poll(job["provider_task_id"])
        except Exception:
            return self._job(job)  # Asking again costs nothing; the task ID is kept.
        if answer.state == "PENDING":
            return self._job(job)
        if answer.state == "FAILED":
            return self._finish(job, "BLOCKED", "Nhà cung cấp báo task final thất bại: " + str(answer.error)[:300], release=True)
        work = self.db.data_root / "final-work" / job["operation_id"]
        try:
            work.mkdir(parents=True, exist_ok=True)
            clip = work / "final.mp4"
            provider.download(answer.video_url, clip)
            with self.db.transaction() as conn:
                episode = self.breakdown._episode(conn, job["episode_id"])
                state = self._state(conn, episode)
                shot = require(next((s for s in state["shots"] if s["shot_id"] == request["shot_id"]), None), "shot")
                # The final belongs to the draft it was made from, even if the shot has moved on since.
                take = self._register(conn, episode, dict(shot, _basis=request["basis_hash"]), clip, "AI_FINAL", job["id"], job["provider_task_id"])
        except (DomainError, OSError) as exc:
            return self._finish(job, "BLOCKED", "Task final đã xong ở nhà cung cấp nhưng không tải hoặc lưu được video: " + str(exc)[:300] + f" Task ID: {job['provider_task_id']}.", billed=True)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        note = self._conform_safely(episode, take, shot)
        return self._finish(job, "COMPLETED", note, billed=True, result={"failed": [{"shot_id": request["shot_id"], "error": note}] if note else []})

    def stop_waiting(self, job_id: str) -> dict:
        with self.db.transaction() as conn:
            job = require(conn.execute("SELECT * FROM final_jobs WHERE id=?", (job_id,)).fetchone(), "final job")
            if job["state"] != "PENDING":
                raise DomainError("Chỉ dừng chờ được yêu cầu đang chờ nhà cung cấp.", 409)
            conn.execute("UPDATE final_jobs SET state='RECONCILING',error=?,updated_at=? WHERE id=?",
                         (f"Đã dừng chờ. Kiểm tra task {job['provider_task_id']} ở nhà cung cấp rồi đóng khoản giữ trong Sổ chi phí.", now(), job_id))
        return self.state(job["episode_id"])

    # ---- step 10: UE finals ----------------------------------------------------------------------------------------

    def enqueue_ue(self, episode_id: str, shot_ids: list[str] | None = None) -> dict:
        self.studio._unreal_adapter()
        self.media.paths()
        with self.db.transaction() as conn:
            episode, state = self._ready(conn, episode_id)
            if not state["color_supported"]:
                raise DomainError(f"Chuẩn màu {state['standards']['color_space']} của series chưa chuẩn hóa được trong app; đổi chuẩn ở S1 hoặc xử lý màu ngoài app.", 409)
            active = conn.execute("SELECT state FROM final_jobs WHERE episode_id=? AND kind='UE_RENDER' AND state IN ('QUEUED','RUNNING','RECONCILING') LIMIT 1", (episode_id,)).fetchone()
            if active:
                raise DomainError("Lần render final trước bị gián đoạn; đóng Unreal cũ và xác nhận trước khi render lại." if active["state"] == "RECONCILING" else "Đang có việc render final cho tập này.", 409)
            pool = [s for s in state["shots"] if s["route"] == "UE"]
            chosen = [s for s in pool if (s["needs_final"] if shot_ids is None else s["shot_id"] in shot_ids)]
            if shot_ids is not None and (len(chosen) != len(set(shot_ids)) or not shot_ids):
                raise DomainError("Danh sách shot không hợp lệ hoặc không thuộc nhánh UE.", 409)
            for shot in chosen:
                if shot["issues"] or not shot["_basis"]:
                    raise DomainError(f"{shot['shot_id']}: " + (" ".join(shot["issues"]) or "cache và proxy chưa chốt C6."), 409)
            if not chosen:
                raise DomainError("Không có shot UE nào đang chờ final: cần scene đã khóa dựng; shot đã có final đúng bản thì được giữ.", 409)
            standards = state["standards"]
            request = {"schema_version": "v45.ue-render-request.1", "quality": "FINAL", "image_format": "PNG", "temporal_samples": FINAL_TEMPORAL_SAMPLES,
                       "project_id": episode["project_id"], "episode_code": episode["code"], "fps": standards["fps"], "width": standards["width"], "height": standards["height"],
                       "color_space": standards["color_space"],
                       "shots": [{"shot_id": s["shot_id"], "sequence_path": s["_view"]["sequence_path"], "map_path": s["_view"]["map_path"], "length_frames": s["length_frames"],
                                  "basis_hash": s["_basis"]} for s in chosen]}
            input_hash = digest(request)
            request["operation_id"] = digest({"input_hash": input_hash, "nonce": uid()})
            request["output_dir"] = str(self.db.data_root / "unreal-renders" / request["operation_id"] / "frames")
            conn.execute("INSERT INTO final_jobs(id,project_id,episode_id,kind,shot_id,provider_kind,model,operation_id,input_hash,request_json,state,stage,provider_task_id,estimated_micro,actual_micro,currency,error,result_json,poll_after,created_at,updated_at) VALUES(?,?,?,'UE_RENDER',NULL,NULL,NULL,?,?,?,'QUEUED','PREPARE',NULL,0,NULL,NULL,NULL,NULL,NULL,?,?)",
                         (uid(), episode["project_id"], episode_id, request["operation_id"], input_hash, canonical(request), now(), now()))
            self.studio.audit(conn, episode["project_id"], "final.ue_queued", episode_id, {"shots": len(chosen)})
        return self.state(episode_id)

    def process_next_ue(self) -> dict | None:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM final_jobs WHERE state='QUEUED' AND kind='UE_RENDER' ORDER BY created_at LIMIT 1").fetchone()
            if not row or gpu_queue.holder(conn):
                return None
            job = dict(row)
            gpu_queue.try_acquire(conn, job["operation_id"], "MRQ")
            conn.execute("UPDATE final_jobs SET state='RUNNING',updated_at=? WHERE id=?", (now(), job["id"]))
        request = json.loads(job["request_json"])
        failed, done = [], 0

        def stage(name: str) -> None:
            with self.db.transaction() as conn:
                conn.execute("UPDATE final_jobs SET stage=?,updated_at=? WHERE id=?", (name, now(), job["id"]))
        try:
            with self.db.connect() as conn:
                episode, state = self._ready(conn, job["episode_id"])
            current = {s["shot_id"]: s for s in state["shots"]}
            if any(shot["shot_id"] not in current or current[shot["shot_id"]]["_basis"] != shot["basis_hash"] or not current[shot["shot_id"]]["picture_locked"] for shot in request["shots"]):
                raise DomainError("Khóa dựng hoặc cache đã đổi sau khi xếp việc render final; xếp lại.", 409)
            result = self.preparer(request)
            prepared = prepared_shots(result, request)
            failed = [{"shot_id": key, "error": str(item.get("error"))[:300]} for key, item in prepared.items() if item.get("status") != "OK"]
            if result.get("manifest"):
                stage("RENDER")
                self.renderer(result["manifest"])
            stage("ENCODE")
            work = Path(request["output_dir"])
            for item in request["shots"]:
                if prepared[item["shot_id"]].get("status") != "OK":
                    continue
                shot = current[item["shot_id"]]
                clip = work / (item["shot_id"] + ".mov")
                try:
                    self.media.encode_master(work / item["shot_id"], clip, request["fps"], item["length_frames"], request["color_space"])
                    with self.db.transaction() as conn:
                        take = self._register(conn, episode, shot, clip, "UE_MRQ", job["id"], None)
                    done += 1
                    note = self._conform_safely(episode, take, shot)
                    if note:
                        failed.append({"shot_id": item["shot_id"], "error": note})
                except (DomainError, OSError) as exc:
                    failed.append({"shot_id": item["shot_id"], "error": str(exc)[:300]})
            shutil.rmtree(work, ignore_errors=True)
            with self.db.transaction() as conn:
                conn.execute("UPDATE final_jobs SET state=?,result_json=?,error=?,updated_at=? WHERE id=?",
                             ("COMPLETED" if done else "BLOCKED", canonical({"failed": failed, "finals": done}), None if done else "Không tạo được final nào; xem lỗi từng shot.", now(), job["id"]))
                gpu_queue.release(conn, job["operation_id"])
                self.studio.audit(conn, job["project_id"], "final.ue_completed", job["id"], {"ok": done, "failed": len(failed)})
        except Exception as exc:
            with self.db.transaction() as conn:
                conn.execute("UPDATE final_jobs SET state='BLOCKED',result_json=?,error=?,updated_at=? WHERE id=?",
                             (canonical({"failed": failed, "finals": done}), str(exc)[:500] if isinstance(exc, DomainError) else f"{type(exc).__name__}: không render được final.", now(), job["id"]))
                gpu_queue.release(conn, job["operation_id"])
        with self.db.connect() as conn:
            return self._job(conn.execute("SELECT * FROM final_jobs WHERE id=?", (job["id"],)).fetchone())

    def release_interrupted(self, job_id: str, worker_stopped: bool) -> dict:
        with self.db.transaction() as conn:
            job = require(conn.execute("SELECT * FROM final_jobs WHERE id=?", (job_id,)).fetchone(), "final job")
            if job["state"] != "RECONCILING" or job["kind"] != "UE_RENDER":
                raise DomainError("Việc render này không ở trạng thái cần xác nhận.", 409)
            if not worker_stopped:
                raise DomainError("Đóng cửa sổ Unreal của lần render cũ rồi xác nhận trong giao diện.", 409)
            conn.execute("UPDATE final_jobs SET state='BLOCKED',error='Bị gián đoạn; render lại khi cần.',updated_at=? WHERE id=?", (now(), job_id))
            gpu_queue.release(conn, job["operation_id"])
        return self.state(job["episode_id"])

    # ---- C8 --------------------------------------------------------------------------------------------------------

    def review_file(self, conform_id: str) -> Path:
        with self.db.connect() as conn:
            row = require(conn.execute("SELECT * FROM conformed_shots WHERE id=?", (conform_id,)).fetchone(), "conformed shot")
        path = Path(self.studio.project(row["project_id"])["root"]).resolve() / row["review_path"]
        if not path.is_file():
            raise DomainError("Bản xem thử của final thiếu trong thư mục dự án; chuẩn hóa lại.", 409)
        return path

    def approve_c8(self, episode_id: str, shot_id: str, expected_hash: str, checks: dict) -> dict:
        """The operator has compared the conformed final with the locked rough cut and accepts it for this shot."""
        with self.db.transaction() as conn:
            episode, state = self._ready(conn, episode_id)
            shot = require(next((s for s in state["shots"] if s["shot_id"] == shot_id), None), "shot")
            if not isinstance(checks, dict) or set(checks) != set(shot["checks"]) or any(checks[key] is not True for key in shot["checks"]):
                raise DomainError("Xác nhận đủ các mục đã so với rough cut: khớp bố cục và nhân vật, không lỗi hình" + ("" if shot["route"] == "UE" else ", không có watermark") + ".")
            if not shot["picture_locked"] or not shot["c8_hash"]:
                raise DomainError("Chưa duyệt được: cần final đã chuẩn hóa, qua kiểm tra tự động và scene còn khóa dựng.", 409)
            if shot["c8_hash"] != expected_hash:
                raise DomainError("Final đã đổi sau khi bạn xem; tải lại.", 409)
            if shot["c8"]["approved"]:
                raise DomainError("Final của shot này đã được duyệt.", 409)
            if file_sha256(state["_root"] / shot["conform"]["relative_path"]) != shot["conform"]["sha256"]:
                raise DomainError("File final đã chuẩn hóa bị đổi ngoài app.", 409)
            sequence = conn.execute("SELECT COALESCE(MAX(exact_version),0)+1 FROM workflow_checkpoints WHERE project_id=? AND checkpoint='C8' AND scope_id=?", (episode["project_id"], shot_id)).fetchone()[0]
            workflow.record_checkpoint(self.studio, conn, episode["project_id"], "C8", shot_id, sequence, expected_hash,
                                       {"branch": shot["route"], "episode_id": episode_id, "final_version": shot["final"]["version"], "provenance": shot["final"]["provenance"],
                                        "provider_task_id": shot["final"]["provider_task_id"], "conform_version": shot["conform"]["version"], "conform_sha256": shot["conform"]["sha256"],
                                        "checks": shot["checks"], "qc": [c["key"] for c in shot["conform"]["qc"]["checks"] if c["ok"]], "watermark_detection": "OPERATOR_VIEWED"})
            conn.execute("INSERT OR REPLACE INTO final_approvals(episode_id,shot_id,project_id,c8_hash,checks_json,created_at) VALUES(?,?,?,?,?,?)",
                         (episode_id, shot_id, episode["project_id"], expected_hash, canonical(shot["checks"]), now()))
        return self.state(episode_id)

    def revoke_c8(self, episode_id: str, shot_id: str) -> dict:
        with self.db.transaction() as conn:
            episode = self.breakdown._episode(conn, episode_id)
            if not conn.execute("DELETE FROM final_approvals WHERE episode_id=? AND shot_id=?", (episode_id, shot_id)).rowcount:
                raise DomainError("Shot này chưa duyệt final.", 409)
            self.studio.audit(conn, episode["project_id"], "final.c8_revoked", episode_id, {"shot": shot_id})
        return self.state(episode_id)

    def approved_masters(self, conn, episode: dict) -> dict:
        """For delivery: the conformed master of every shot whose final passed C8."""
        state = self._state(conn, episode)
        return {s["shot_id"]: s["conform"] for s in state["shots"] if s["c8"]["approved"]}
