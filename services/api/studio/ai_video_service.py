"""Workflow V4.5 zone B, AI branch and the AI half of Hybrid: 07 inputs, 08 drafts, C5 review, 09/C6 lock.

Only drafts are created here. A final is made after Picture Lock (C7), from the draft that C6 fixed, so
this module also keeps the provider task ID of every draft and when it stops being usable.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import budget, workflow
from .ai_video_provider import MiniMaxProvider, SeedanceProvider, VideoNotSent, VideoOutcomeUnknown
from .domain import DomainError, canonical, digest, file_sha256
from .route_domain import MODEL_LABELS, MODEL_LIMITS
from .service import now, require, uid
from .style_domain import banned_hits, build_prompt, probe_image
from .tool_settings import ToolSettings
from .video_model_settings import MODELS, VideoModelSettings


CHECKS = ("characters", "action", "camera", "style")
SHOT_SIZE_EN = {"EXTREME_WIDE": "extreme wide shot", "WIDE": "wide shot", "MEDIUM": "medium shot", "CLOSE_UP": "close-up",
                "EXTREME_CLOSE_UP": "extreme close-up", "OVER_SHOULDER": "over-the-shoulder shot", "TWO_SHOT": "two shot", "INSERT": "insert shot"}
POLL_SECONDS = 10
EXPIRY_WARNING_HOURS = 48
VIDEO_SUFFIXES = (".mp4", ".mov")


def _is_video(path: Path) -> bool:
    with open(path, "rb") as stream:
        return stream.read(12)[4:8] == b"ftyp"


class AiVideoService:
    def __init__(self, studio, breakdown, animation, ue, *, settings=None, provider_factory=None, prober=None):
        self.studio = studio
        self.db = studio.db
        self.breakdown = breakdown
        self.animation = animation
        self.ue = ue
        self.settings = settings or VideoModelSettings(studio)
        self.provider_factory = provider_factory or self._provider
        self.prober = prober or self._ffprobe
        with self.db.transaction() as conn:
            # A submit interrupted by a restart may or may not have created a task; it is never resent.
            conn.execute("UPDATE ai_video_jobs SET state='RECONCILING',error='Backend khởi động lại trong lúc gửi yêu cầu video.',updated_at=? WHERE state='RUNNING'", (now(),))

    def _provider(self, model: str, config: dict):
        reader = lambda: self.settings.key_reader(config["secret"])
        cls = SeedanceProvider if model == "SEEDANCE_2_5" else MiniMaxProvider
        return cls(("SEEDANCE_" if model == "SEEDANCE_2_5" else "MINIMAX_") + config["platform"], config["base_url"], reader)

    def _ffprobe(self, path: Path) -> float | None:
        tools = ToolSettings(self.studio).get()
        if not tools["configured"] or not tools["ffprobe_path"]:
            return None
        try:
            done = subprocess.run([tools["ffprobe_path"], "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                                  capture_output=True, text=True, timeout=60, check=False)
            return round(float(done.stdout.strip()), 3) if done.returncode == 0 else None
        except (OSError, ValueError, subprocess.SubprocessError):
            return None

    # ---- 07 inputs -------------------------------------------------------------------------------------------------

    def _view(self, conn, plan: dict, shot: dict, configs: dict, sources: dict) -> dict:
        """Everything about one AI or Hybrid shot: what would be sent, what it costs, what exists, what blocks it."""
        episode = plan["episode"]
        root = Path(self.studio.project(episode["project_id"])["root"]).resolve()
        row = conn.execute("SELECT * FROM ai_shot_inputs WHERE episode_id=? AND shot_id=?", (episode["id"], shot["shot_id"])).fetchone()
        model, style = shot["video_model"], plan["style"]
        config = configs[model]
        members = [c for c in shot["characters"] if c["entity_key"]] + ([shot["environment"]] if shot["environment"]["entity_key"] else [])
        override = row["prompt_override"] if row and row["prompt_override"] else None
        described = [f"{m['display_name']}: {style['entities'][m['entity_key']]['prompt_description']}" for m in members
                     if style["entities"].get(m["entity_key"], {}).get("prompt_description")]
        subject = override or shot["prompt_en"] or shot["description_en"]
        prompt = build_prompt(", ".join(filter(None, [subject, SHOT_SIZE_EN[shot["shot_size"]] if not override else "", *([] if override else described)])), style["phrases"]) if subject else ""
        available = [{"key": f"{m['entity_key']}/{ref['role']}", "entity": m["display_name"], "role": ref["role"], "relative_path": ref["relative_path"], "sha256": ref["sha256"],
                      "file_ok": (root / ref["relative_path"]).is_file()}
                     for m in members for ref in style["entities"].get(m["entity_key"], {}).get("references", [])]
        limit = (SeedanceProvider if model == "SEEDANCE_2_5" else MiniMaxProvider).max_references
        chosen = json.loads(row["reference_keys_json"]) if row and row["reference_keys_json"] is not None else None
        if chosen is None:
            # Default: one picture per character and one of the environment, as many as the model takes.
            firsts = {}
            for ref in available:
                firsts.setdefault(ref["key"].split("/")[0], ref["key"])
            chosen = list(firsts.values())[:limit]
        references = [ref for ref in available if ref["key"] in chosen][:limit]
        control_row = conn.execute("SELECT * FROM hybrid_control_inputs WHERE episode_id=? AND shot_id=? ORDER BY version DESC LIMIT 1", (episode["id"], shot["shot_id"])).fetchone()
        control = {"id": control_row["id"], "kind": control_row["kind"], "version": control_row["version"], "relative_path": control_row["relative_path"],
                   "sha256": control_row["sha256"], "file_ok": (root / control_row["relative_path"]).is_file()} if control_row and shot["route"] == "HYBRID" else None
        first_frame = control if control and control["kind"] == "FIRST_FRAME" else None
        limits, price = MODEL_LIMITS[model], plan["prices"]["video"][model]
        seconds = max(limits["min_seconds"], math.ceil(shot["seconds"]))
        estimate = seconds * (price["draft_micro_per_second"] + (price["input_video_micro_per_second"] if control and control["kind"] == "VIDEO" else 0))
        content_hash = digest({"prompt": prompt, "model": model, "route": shot["route"], "references": [] if first_frame else [r["sha256"] for r in references],
                               "control": control["sha256"] if control else None})
        issues = []
        if not config["configured"]:
            issues.append(f"{MODEL_LABELS[model]} chưa được cấu hình đủ nền tảng, model ID và khóa ở Cài đặt → Model video AI.")
        if not prompt:
            issues.append("Shot chưa có prompt tiếng Anh; nhập prompt cho shot này.")
        hits = banned_hits(prompt, style["banned"])
        if hits:
            issues.append("Prompt chứa từ cấm của Style bible: " + ", ".join(hits) + ".")
        if limits["max_seconds"] and seconds > limits["max_seconds"]:
            issues.append(f"Shot dài {shot['seconds']:g} giây, vượt clip tối đa {limits['max_seconds']} giây của {MODEL_LABELS[model]}; đổi model ở C3 hoặc rút ngắn shot ở C4.")
        if not price["draft_micro_per_second"]:
            issues.append(f"Chưa có giá draft của {MODEL_LABELS[model]} trong bảng giá ở C3.")
        if any(not ref["file_ok"] for ref in references) or (control and not control["file_ok"]):
            issues.append("File tham chiếu hoặc nguồn UE thiếu trong thư mục dự án.")
        source = None
        if shot["route"] == "HYBRID":
            source = sources.get(shot["shot_id"], {"locked": False, "notes": None})
            if not source["locked"]:
                issues.append("Nguồn UE của shot Hybrid chưa được chốt ở C5.")
            if not control:
                issues.append("Chưa đăng ký video nguồn UE hoặc frame đầu của shot Hybrid.")
            elif control["kind"] == "VIDEO":
                issues.append("Chưa có đường gửi video nguồn UE lên nhà cung cấp được kiểm chứng; đăng ký frame đầu (ảnh) của nguồn UE để tạo draft.")
        takes, current_time = [], datetime.now(timezone.utc)
        # The 7-day window only limits making a final from a draft; a draft that already has its final stays usable.
        finalized = {r[0] for r in conn.execute("SELECT DISTINCT draft_take_id FROM final_takes WHERE episode_id=? AND shot_id=? AND draft_take_id IS NOT NULL", (episode["id"], shot["shot_id"]))}
        for take in conn.execute("SELECT * FROM ai_video_takes WHERE episode_id=? AND shot_id=? ORDER BY version DESC", (episode["id"], shot["shot_id"])):
            expires = datetime.fromisoformat(take["expires_at"]) if take["expires_at"] else None
            hours = (expires - current_time).total_seconds() / 3600 if expires else None
            usable = take["content_hash"] == content_hash and take["requested_seconds"] >= shot["seconds"]
            takes.append({"id": take["id"], "version": take["version"], "review": take["review"], "model": take["model"], "provider_task_id": take["provider_task_id"],
                          "requested_seconds": take["requested_seconds"], "measured_seconds": take["measured_seconds"], "prompt": take["prompt"], "created_at": take["created_at"],
                          "checks": json.loads(take["checks_json"]), "current": usable, "file_ok": (root / take["relative_path"]).is_file(),
                          "expires_at": take["expires_at"], "expires_in_hours": round(hours, 1) if hours is not None else None,
                          "expired": hours is not None and hours <= 0, "expiring": hours is not None and 0 < hours <= EXPIRY_WARNING_HOURS,
                          "has_final": take["id"] in finalized, "locked": bool(row and row["c6_take_id"] == take["id"])})
        job = conn.execute("SELECT id,state,error,provider_task_id FROM ai_video_jobs WHERE episode_id=? AND shot_id=? ORDER BY created_at DESC LIMIT 1", (episode["id"], shot["shot_id"])).fetchone()
        locked = next((t for t in takes if t["locked"]), None)
        waiting_final = bool(locked and not locked["has_final"])
        return {"shot_id": shot["shot_id"], "route": shot["route"], "model": model, "model_label": MODEL_LABELS[model], "description": shot["description"],
                "seconds": shot["seconds"], "generate_seconds": seconds, "resolution": (SeedanceProvider if model == "SEEDANCE_2_5" else MiniMaxProvider).draft_resolution,
                "prompt": prompt, "prompt_override": override, "references": [dict(ref, selected=ref in references) for ref in available], "reference_limit": limit,
                "first_frame_only": bool(first_frame), "control": control, "ue_source": source, "revision": row["revision"] if row else 0,
                "estimated_micro": estimate, "currency": price["currency"], "content_hash": content_hash, "issues": issues, "takes": takes,
                "job": dict(job) if job else None,
                "c6": {"locked": bool(locked and locked["current"] and locked["review"] == "APPROVED" and locked["file_ok"] and not (waiting_final and locked["expired"])),
                       "take_id": locked["id"] if locked else None, "stale": bool(locked and not locked["current"]),
                       "expired": waiting_final and locked["expired"], "expiring": waiting_final and locked["expiring"]},
                "_references": references, "_first_frame": first_frame, "_control": control, "_config": config, "_root": root}

    def _state(self, conn, episode: dict) -> dict:
        plan = self.animation.plan(conn, episode)
        configs = {model: self.settings.one(model) for model in MODELS}
        models = [{k: configs[m][k] for k in ("model", "label", "platform", "configured", "verified_at")} for m in MODELS]
        jobs = [dict(r) for r in conn.execute("SELECT id,shot_id,model,state,estimated_micro,actual_micro,currency,error,updated_at FROM ai_video_jobs WHERE episode_id=? ORDER BY created_at DESC LIMIT 8", (episode["id"],))]
        base = {"episode": episode, "ready": plan["ready"], "blockers": plan["blockers"], "models": models, "shots": [], "jobs": jobs,
                "summary": {"shots": 0, "with_draft": 0, "approved": 0, "locked": 0, "expiring": 0, "working": 0}}
        if not plan["ready"]:
            return base
        # The UE source of a Hybrid shot has to be locked at C5 before an AI draft is made from it.
        sources = {s["shot_id"]: {"locked": s["c5"]["locked"], "notes": s["c5"]["notes"]} for s in self.ue._state(conn, episode)["shots"]}             if any(shot["route"] == "HYBRID" for shot in plan["shots"]) else {}
        shots = [self._view(conn, plan, shot, configs, sources) for shot in plan["shots"] if shot["route"] != "UE"]
        summary = {"shots": len(shots), "with_draft": sum(any(t["current"] for t in s["takes"]) for s in shots),
                   "approved": sum(any(t["current"] and t["review"] == "APPROVED" for t in s["takes"]) for s in shots),
                   "locked": sum(s["c6"]["locked"] for s in shots), "expiring": sum(s["c6"]["expiring"] or s["c6"]["expired"] for s in shots),
                   "working": sum(bool(s["job"] and s["job"]["state"] in ("QUEUED", "RUNNING", "PENDING")) for s in shots)}
        return {**base, "shots": shots, "summary": summary, "_plan": plan,
                "audio_note": "Mọi yêu cầu draft tắt audio tự sinh; thoại của phim là take ElevenLabs đã duyệt ở C4.",
                "final_note": "Ở bước này chỉ tạo draft. Final được tạo sau khi khóa dựng (C7), từ draft đã chốt."}

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
            raise DomainError("Chưa làm việc được với shot AI: " + " ".join(state["blockers"]), 409)
        return episode, state, require(next((s for s in state["shots"] if s["shot_id"] == shot_id), None), "AI shot")

    def save_input(self, episode_id: str, shot_id: str, expected_revision: int, prompt_override: str | None, reference_keys: list | None) -> dict:
        if prompt_override is not None and (not isinstance(prompt_override, str) or len(prompt_override) > 1500):
            raise DomainError("Prompt tối đa 1500 ký tự.")
        with self.db.transaction() as conn:
            episode, _, shot = self._shot(conn, episode_id, shot_id)
            if shot["revision"] != expected_revision:
                raise DomainError("Đầu vào của shot này đã đổi ở phiên khác; tải lại.", 409)
            known = {ref["key"] for ref in shot["references"]}
            if reference_keys is not None and (not isinstance(reference_keys, list) or len(set(reference_keys)) != len(reference_keys)
                                               or any(key not in known for key in reference_keys) or len(reference_keys) > shot["reference_limit"]):
                raise DomainError(f"Chọn tối đa {shot['reference_limit']} ảnh tham chiếu trong các ảnh đã duyệt của Style bible cho shot này.", 409)
            conn.execute("INSERT INTO ai_shot_inputs(episode_id,shot_id,project_id,prompt_override,reference_keys_json,c6_take_id,revision,updated_at) VALUES(?,?,?,?,?,NULL,1,?) "
                         "ON CONFLICT(episode_id,shot_id) DO UPDATE SET prompt_override=excluded.prompt_override,reference_keys_json=excluded.reference_keys_json,revision=revision+1,updated_at=excluded.updated_at",
                         (episode_id, shot_id, episode["project_id"], (prompt_override or "").strip() or None, canonical(reference_keys) if reference_keys is not None else None, now()))
            self.studio.audit(conn, episode["project_id"], "ai_video.input_saved", episode_id, {"shot": shot_id})
        return self.state(episode_id)

    def register_control(self, episode_id: str, shot_id: str, source_path: str) -> dict:
        """Copy a UE-rendered source of a Hybrid shot into the project: a control video or its first frame."""
        source = Path(source_path)
        if not source.is_file():
            raise DomainError("Không tìm thấy file đã chọn.")
        suffix = source.suffix.lower()
        if suffix in VIDEO_SUFFIXES:
            if not _is_video(source):
                raise DomainError("File đã chọn không phải video MP4/MOV hợp lệ.")
            kind = "VIDEO"
        else:
            kind, suffix = "FIRST_FRAME", "." + probe_image(source)[0]
        with self.db.transaction() as conn:
            episode, _, shot = self._shot(conn, episode_id, shot_id)
            if shot["route"] != "HYBRID":
                raise DomainError("Chỉ shot Hybrid mới có nguồn UE điều khiển.", 409)
            version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM hybrid_control_inputs WHERE episode_id=? AND shot_id=?", (episode_id, shot_id)).fetchone()[0]
            relative = f"media/hybrid/{episode['code']}/{shot_id}/control-v{version}{suffix}"
            target = shot["_root"] / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise DomainError("Đã có file nguồn UE cùng tên trong dự án; kiểm tra thư mục media/hybrid.", 409)
            shutil.copyfile(source, target)
            conn.execute("INSERT INTO hybrid_control_inputs(id,project_id,episode_id,shot_id,version,kind,relative_path,sha256,bytes,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (uid(), episode["project_id"], episode_id, shot_id, version, kind, relative, file_sha256(target), target.stat().st_size, now()))
            self.studio.audit(conn, episode["project_id"], "ai_video.control_registered", episode_id, {"shot": shot_id, "kind": kind})
        return self.state(episode_id)

    # ---- 08 drafts -------------------------------------------------------------------------------------------------

    def enqueue_draft(self, episode_id: str, shot_id: str, client_request_id: str) -> dict:
        try:
            uuid.UUID(client_request_id)
        except (ValueError, AttributeError) as exc:
            raise DomainError("client_request_id phải là UUID.") from exc
        with self.db.transaction() as conn:
            episode, state, shot = self._shot(conn, episode_id, shot_id)
            if shot["issues"]:
                raise DomainError(f"Chưa tạo được draft cho {shot_id}: " + " ".join(shot["issues"]), 409)
            pending = conn.execute("SELECT state FROM ai_video_jobs WHERE episode_id=? AND shot_id=? AND state IN ('QUEUED','RUNNING','PENDING','RECONCILING') LIMIT 1", (episode_id, shot_id)).fetchone()
            if pending:
                raise DomainError("Lần tạo trước chưa rõ kết quả; kiểm tra tài khoản nhà cung cấp rồi đóng khoản giữ trong Sổ chi phí trước khi gửi lại." if pending["state"] == "RECONCILING"
                                  else "Đang có yêu cầu draft cho shot này.", 409)
            config = shot["_config"]
            standards = conn.execute("SELECT width,height FROM series_standards WHERE project_id=?", (episode["project_id"],)).fetchone()
            divisor = math.gcd(standards["width"], standards["height"])
            request = {"schema_version": "ai-video.request.1", "episode_id": episode_id, "shot_id": shot_id, "stage": "DRAFT", "route": shot["route"],
                       "model": shot["model"], "model_id": config["model_id"], "platform": config["platform"], "prompt": shot["prompt"],
                       "seconds": shot["generate_seconds"], "resolution": shot["resolution"], "ratio": f"{standards['width'] // divisor}:{standards['height'] // divisor}",
                       "references": [{"key": r["key"], "path": str(shot["_root"] / r["relative_path"]), "sha256": r["sha256"]} for r in shot["_references"]],
                       "first_frame": {"path": str(shot["_root"] / shot["_first_frame"]["relative_path"]), "sha256": shot["_first_frame"]["sha256"]} if shot["_first_frame"] else None,
                       "content_hash": shot["content_hash"], "generate_audio": False}
            input_hash = digest(request)
            operation_id = digest({"input_hash": input_hash, "client_request_id": client_request_id})
            if conn.execute("SELECT 1 FROM ai_video_jobs WHERE operation_id=?", (operation_id,)).fetchone():
                return self._public(state)
            provider = self.provider_factory(shot["model"], config)
            budget.reserve(conn, episode["project_id"], operation_id, gate=2, category="VIDEO_DRAFT", provider_kind=provider.kind,
                           currency=shot["currency"], amount_micro=shot["estimated_micro"])
            request["operation_id"] = operation_id
            conn.execute("INSERT INTO ai_video_jobs(id,project_id,episode_id,shot_id,stage,provider_kind,model,operation_id,input_hash,request_json,state,provider_task_id,estimated_micro,actual_micro,currency,take_id,error,poll_after,created_at,updated_at) VALUES(?,?,?,?,'DRAFT',?,?,?,?,?,'QUEUED',NULL,?,NULL,?,NULL,NULL,NULL,?,?)",
                         (uid(), episode["project_id"], episode_id, shot_id, provider.kind, shot["model"], operation_id, input_hash, canonical(request),
                          shot["estimated_micro"], shot["currency"], now(), now()))
            self.studio.audit(conn, episode["project_id"], "ai_video.queued", episode_id, {"shot": shot_id, "model": shot["model"], "estimate_micro": shot["estimated_micro"]})
        return self.state(episode_id)

    def _finish(self, job: dict, state: str, error: str | None, *, billed: bool = False, release: bool = False, take_id: str | None = None) -> dict:
        with self.db.transaction() as conn:
            conn.execute("UPDATE ai_video_jobs SET state=?,error=?,actual_micro=CASE WHEN ? THEN estimated_micro ELSE actual_micro END,take_id=COALESCE(?,take_id),updated_at=? WHERE id=?",
                         (state, error, int(billed), take_id, now(), job["id"]))
            if billed:
                # Billed per second generated, at the price the operator entered; not the provider's invoice.
                budget.settle(conn, job["operation_id"], job["estimated_micro"])
            elif release:
                budget.release(conn, job["operation_id"])
            self.studio.audit(conn, job["project_id"], "ai_video." + state.lower(), job["id"], {"shot": job["shot_id"]})
            return dict(conn.execute("SELECT * FROM ai_video_jobs WHERE id=?", (job["id"],)).fetchone())

    def _job_provider(self, job: dict):
        return self.provider_factory(job["model"], self.settings.one(job["model"]))

    def process_next(self) -> dict | None:
        """Submit one queued draft. After this the job waits for the provider (PENDING) and is polled separately."""
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM ai_video_jobs WHERE state='QUEUED' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            job = dict(row)
            conn.execute("UPDATE ai_video_jobs SET state='RUNNING',updated_at=? WHERE id=?", (now(), job["id"]))
        request = json.loads(job["request_json"])
        try:
            with self.db.connect() as conn:
                _, _, shot = self._shot(conn, job["episode_id"], job["shot_id"])
            if shot["content_hash"] != request["content_hash"] or shot["generate_seconds"] != request["seconds"] or shot["issues"]:
                raise VideoNotSent("Prompt, tham chiếu, timing hoặc cấu hình đã đổi sau khi xếp việc; yêu cầu chưa gửi.", 409)
            task_id = self._job_provider(job).submit(request)
        except VideoNotSent as exc:
            return self._finish(job, "BLOCKED", str(exc)[:500], release=True)
        except VideoOutcomeUnknown as exc:
            return self._finish(job, "RECONCILING", str(exc)[:500])
        except DomainError as exc:
            return self._finish(job, "BLOCKED", str(exc)[:500], release=True)
        except Exception as exc:
            return self._finish(job, "RECONCILING", f"{type(exc).__name__}: không rõ yêu cầu đã tới nhà cung cấp hay chưa.")
        with self.db.transaction() as conn:
            first_poll = (datetime.now(timezone.utc) + timedelta(seconds=POLL_SECONDS)).isoformat()
            conn.execute("UPDATE ai_video_jobs SET state='PENDING',provider_task_id=?,poll_after=?,updated_at=? WHERE id=?", (task_id, first_poll, now(), job["id"]))
            return dict(conn.execute("SELECT * FROM ai_video_jobs WHERE id=?", (job["id"],)).fetchone())

    def poll_next(self) -> dict | None:
        """Ask the provider about one waiting task; a finished one is downloaded and becomes a take to review."""
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM ai_video_jobs WHERE state='PENDING' AND poll_after<=? ORDER BY poll_after LIMIT 1", (now(),)).fetchone()
            if not row:
                return None
            job = dict(row)
            later = (datetime.now(timezone.utc) + timedelta(seconds=POLL_SECONDS)).isoformat()
            conn.execute("UPDATE ai_video_jobs SET poll_after=?,updated_at=? WHERE id=?", (later, now(), job["id"]))
        request = json.loads(job["request_json"])
        try:
            provider = self._job_provider(job)
            answer = provider.poll(job["provider_task_id"])
        except Exception:
            return job  # Asking again costs nothing; the task ID is kept.
        if answer.state == "PENDING":
            return job
        if answer.state == "FAILED":
            return self._finish(job, "BLOCKED", "Nhà cung cấp báo task thất bại: " + str(answer.error)[:300], release=True)
        episode_code = job["shot_id"].split("_", 1)[0]
        root = Path(self.studio.project(job["project_id"])["root"]).resolve()
        try:
            with self.db.connect() as conn:
                version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM ai_video_takes WHERE episode_id=? AND shot_id=?", (job["episode_id"], job["shot_id"])).fetchone()[0]
            relative = f"media/ai/{episode_code}/{job['shot_id']}/draft-v{version}.mp4"
            target = root / relative
            if target.exists():
                relative = f"media/ai/{episode_code}/{job['shot_id']}/draft-v{version}-{job['operation_id'][:8]}.mp4"
                target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            provider.download(answer.video_url, target)
            if not _is_video(target):
                target.unlink(missing_ok=True)
                raise DomainError("File nhà cung cấp trả về không phải video MP4.", 409)
        except (DomainError, OSError) as exc:
            # The task succeeded at the provider, so it is billed; only the download failed here.
            return self._finish(job, "BLOCKED", "Task đã xong ở nhà cung cấp nhưng không tải được video: " + str(exc)[:300] + f" Task ID: {job['provider_task_id']}.", billed=True)
        except Exception as exc:
            return self._finish(job, "BLOCKED", f"Task đã xong ở nhà cung cấp nhưng không tải được video ({type(exc).__name__}). Task ID: {job['provider_task_id']}.", billed=True)
        days = provider.draft_expiry_days
        take_id = uid()
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO ai_video_takes(id,project_id,episode_id,shot_id,version,job_id,stage,route,provider_kind,model,provider_task_id,expires_at,content_hash,prompt,requested_seconds,measured_seconds,relative_path,sha256,bytes,review,checks_json,created_at) VALUES(?,?,?,?,?,?,'DRAFT',?,?,?,?,?,?,?,?,?,?,?,?,'NEEDS_REVIEW','{}',?)",
                         (take_id, job["project_id"], job["episode_id"], job["shot_id"], version, job["id"], request["route"], job["provider_kind"], job["model"], job["provider_task_id"],
                          (datetime.fromisoformat(job["created_at"]) + timedelta(days=days)).isoformat() if days else None, request["content_hash"], request["prompt"],
                          request["seconds"], self.prober(target), relative, file_sha256(target), target.stat().st_size, now()))
            conn.execute("UPDATE video_model_settings SET verified_at=? WHERE model=? AND verified_at IS NULL", (now(), job["model"]))
        return self._finish(job, "COMPLETED", None, billed=True, take_id=take_id)

    def stop_waiting(self, job_id: str) -> dict:
        """Give up polling a task that never finishes. Its cost stays reserved until the operator settles it in the ledger."""
        with self.db.transaction() as conn:
            job = require(conn.execute("SELECT * FROM ai_video_jobs WHERE id=?", (job_id,)).fetchone(), "draft job")
            if job["state"] != "PENDING":
                raise DomainError("Chỉ dừng chờ được yêu cầu đang chờ nhà cung cấp.", 409)
            conn.execute("UPDATE ai_video_jobs SET state='RECONCILING',error=?,updated_at=? WHERE id=?",
                         (f"Đã dừng chờ. Kiểm tra task {job['provider_task_id']} ở nhà cung cấp rồi đóng khoản giữ trong Sổ chi phí.", now(), job_id))
        return self.state(job["episode_id"])

    # ---- C5 review, 09/C6 lock -------------------------------------------------------------------------------------

    def _take(self, conn, take_id: str) -> dict:
        return dict(require(conn.execute("SELECT * FROM ai_video_takes WHERE id=?", (take_id,)).fetchone(), "draft take"))

    def take_file(self, take_id: str) -> Path:
        with self.db.connect() as conn:
            take = self._take(conn, take_id)
        path = Path(self.studio.project(take["project_id"])["root"]).resolve() / take["relative_path"]
        if not path.is_file() or path.stat().st_size != take["bytes"]:
            raise DomainError("File draft thiếu hoặc đã bị đổi ngoài app.", 409)
        return path

    def control_file(self, control_id: str) -> Path:
        with self.db.connect() as conn:
            row = require(conn.execute("SELECT * FROM hybrid_control_inputs WHERE id=?", (control_id,)).fetchone(), "control input")
        path = Path(self.studio.project(row["project_id"])["root"]).resolve() / row["relative_path"]
        if not path.is_file() or path.stat().st_size != row["bytes"]:
            raise DomainError("File nguồn UE thiếu hoặc đã bị đổi ngoài app.", 409)
        return path

    def review_take(self, take_id: str, decision: str, checks: dict) -> dict:
        """C5 for an AI shot: the operator has watched the draft and judged characters, action, camera and style."""
        if decision not in ("APPROVED", "REJECTED") or not isinstance(checks, dict) or set(checks) - set(CHECKS) or any(type(v) is not bool for v in checks.values()):
            raise DomainError("Quyết định duyệt draft không hợp lệ.")
        if decision == "APPROVED":
            if file_sha256(self.take_file(take_id)) != self._take_sha(take_id):
                raise DomainError("File draft đã bị đổi ngoài app.", 409)
            if any(checks.get(key) is not True for key in CHECKS):
                raise DomainError("Duyệt draft cần xác nhận đủ: nhân vật, hành động, camera và style.")
        with self.db.transaction() as conn:
            take = self._take(conn, take_id)
            episode, _, shot = self._shot(conn, take["episode_id"], take["shot_id"])
            view = next(t for t in shot["takes"] if t["id"] == take_id)
            if decision == "APPROVED" and not view["current"]:
                raise DomainError("Draft này tạo từ prompt, tham chiếu hoặc timing cũ; tạo draft mới rồi duyệt.", 409)
            conn.execute("UPDATE ai_video_takes SET review=?,checks_json=? WHERE id=?", (decision, canonical(checks), take_id))
            if decision == "REJECTED":
                conn.execute("UPDATE ai_shot_inputs SET c6_take_id=NULL,revision=revision+1,updated_at=? WHERE episode_id=? AND shot_id=? AND c6_take_id=?", (now(), take["episode_id"], take["shot_id"], take_id))
            elif shot["route"] == "AI":
                sequence = conn.execute("SELECT COALESCE(MAX(exact_version),0)+1 FROM workflow_checkpoints WHERE project_id=? AND checkpoint='C5' AND scope_id=?", (episode["project_id"], take["shot_id"])).fetchone()[0]
                workflow.record_checkpoint(self.studio, conn, episode["project_id"], "C5", take["shot_id"], sequence, take["sha256"],
                                           {"branch": "AI", "episode_id": take["episode_id"], "take_id": take_id, "take_version": take["version"], "model": take["model"], "checks": list(CHECKS)})
            self.studio.audit(conn, episode["project_id"], "ai_video.take_reviewed", take_id, {"shot": take["shot_id"], "decision": decision})
        return self.state(take["episode_id"])

    def _take_sha(self, take_id: str) -> str:
        with self.db.connect() as conn:
            return self._take(conn, take_id)["sha256"]

    def lock_draft(self, episode_id: str, shot_id: str, take_id: str) -> dict:
        """09/C6: this draft is the one the rough cut uses and the final will be made from."""
        with self.db.transaction() as conn:
            episode, _, shot = self._shot(conn, episode_id, shot_id)
            view = require(next((t for t in shot["takes"] if t["id"] == take_id), None), "draft take")
            if view["review"] != "APPROVED" or not view["current"] or not view["file_ok"]:
                raise DomainError("Chỉ chốt được draft đã duyệt, đúng prompt và timing hiện tại, còn file trong dự án.", 409)
            if view["expired"] and not view["has_final"]:
                raise DomainError("Draft này đã quá hạn dùng để tạo final; tạo draft mới.", 409)
            take = self._take(conn, take_id)
            conn.execute("INSERT INTO ai_shot_inputs(episode_id,shot_id,project_id,prompt_override,reference_keys_json,c6_take_id,revision,updated_at) VALUES(?,?,?,NULL,NULL,?,1,?) "
                         "ON CONFLICT(episode_id,shot_id) DO UPDATE SET c6_take_id=excluded.c6_take_id,revision=revision+1,updated_at=excluded.updated_at",
                         (episode_id, shot_id, episode["project_id"], take_id, now()))
            sequence = conn.execute("SELECT COALESCE(MAX(exact_version),0)+1 FROM workflow_checkpoints WHERE project_id=? AND checkpoint='C6' AND scope_id=?", (episode["project_id"], shot_id)).fetchone()[0]
            workflow.record_checkpoint(self.studio, conn, episode["project_id"], "C6", shot_id, sequence, take["sha256"],
                                       {"branch": shot["route"], "episode_id": episode_id, "take_id": take_id, "take_version": take["version"], "model": take["model"],
                                        "provider_task_id": take["provider_task_id"], "expires_at": take["expires_at"], "requested_seconds": take["requested_seconds"],
                                        "measured_seconds": take["measured_seconds"], "final_created": False})
        return self.state(episode_id)

    def unlock_draft(self, episode_id: str, shot_id: str) -> dict:
        with self.db.transaction() as conn:
            episode = self.breakdown._episode(conn, episode_id)
            if not conn.execute("UPDATE ai_shot_inputs SET c6_take_id=NULL,revision=revision+1,updated_at=? WHERE episode_id=? AND shot_id=? AND c6_take_id IS NOT NULL", (now(), episode_id, shot_id)).rowcount:
                raise DomainError("Shot này chưa chốt draft.", 409)
            self.studio.audit(conn, episode["project_id"], "ai_video.c6_unlocked", episode_id, {"shot": shot_id})
        return self.state(episode_id)
