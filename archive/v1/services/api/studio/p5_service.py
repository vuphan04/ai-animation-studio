"""Durable screenplay drafts; planner output has no production authority."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import budget
from .domain import DomainError, canonical, digest
from .p5_domain import parse_response, validate_draft
from .planner_adapter import LocalFixturePlanner, PlannerProvider
from .openai_planner import OpenAIPlanner, PlannerNotSent, PlannerRemoteError
from .ai_settings import AISettings
from .secrets import get_provider_key
from .service import Studio, now, require, uid


class PlanningService:
    def __init__(self, studio: Studio, provider: PlannerProvider | None = None, *, secret_available=None):
        self.studio = studio
        self.db = studio.db
        self.provider = provider or LocalFixturePlanner()
        self.custom_provider = provider is not None
        self.ai_settings = AISettings(studio)
        self.openai_provider = OpenAIPlanner()
        self.secret_available = secret_available or (lambda kind: bool(get_provider_key(kind)) if kind == "OPENAI" else False)
        with self.db.transaction() as conn:
            conn.execute("UPDATE planning_jobs SET state='RECONCILING',error='backend restarted during planner attempt',updated_at=? WHERE state='RUNNING'", (now(),))
            conn.execute("UPDATE planning_attempts SET state='RECONCILING',ended_at=? WHERE state='RUNNING'", (now(),))

    def ingest(self, project_id: str, episode_id: str, text: str, language: str,
               target_language: str, estimated_seconds: int, *, max_chars: int = 30_000, max_seconds: int = 180) -> dict:
        self.studio.project(project_id)
        if not isinstance(text, str) or not 40 <= len(text) <= max_chars or not isinstance(language, str) or not isinstance(target_language, str):
            raise DomainError(f"Screenplay cần 40–{max_chars} ký tự và mã ngôn ngữ hợp lệ.")
        if not 2 <= len(language) <= 16 or not 2 <= len(target_language) <= 16 or type(estimated_seconds) is not int or not 30 <= estimated_seconds <= max_seconds:
            raise DomainError(f"Ngôn ngữ hoặc thời lượng screenplay 30–{max_seconds} giây không hợp lệ.")
        with self.db.transaction() as conn:
            require(conn.execute("SELECT 1 FROM episodes WHERE id=? AND project_id=?", (episode_id, project_id)).fetchone(), "episode")
            version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM screenplay_versions WHERE episode_id=?", (episode_id,)).fetchone()[0]
            item = {"id": uid(), "project_id": project_id, "episode_id": episode_id, "version": version,
                    "language": language, "target_language": target_language, "text": text,
                    "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "estimated_seconds": estimated_seconds, "created_at": now()}
            conn.execute("INSERT INTO screenplay_versions(id,project_id,episode_id,version,language,target_language,text,sha256,estimated_seconds,created_at) VALUES(:id,:project_id,:episode_id,:version,:language,:target_language,:text,:sha256,:estimated_seconds,:created_at)", item)
            self.studio.audit(conn, project_id, "screenplay.ingested", item["id"], {"version": version, "sha256": item["sha256"]})
            return item

    def screenplays(self, episode_id: str) -> list[dict]:
        with self.db.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM screenplay_versions WHERE episode_id=? ORDER BY version DESC", (episode_id,))]

    def create_grant(self, project_id: str, provider_kind: str, max_cost_microusd: int, expires_at: str) -> dict:
        self.studio.project(project_id)
        if not provider_kind or provider_kind == LocalFixturePlanner.kind or type(max_cost_microusd) is not int or max_cost_microusd <= 0:
            raise DomainError("Budget grant chỉ dành cho planning provider có phí và hạn mức dương.")
        try:
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except (ValueError, AttributeError) as exc:
            raise DomainError("Budget grant cần thời hạn ISO-8601.") from exc
        if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
            raise DomainError("Budget grant đã hết hạn hoặc thiếu múi giờ.")
        with self.db.transaction() as conn:
            item = {"id": uid(), "project_id": project_id, "provider_kind": provider_kind,
                    "max_cost_microusd": max_cost_microusd, "reserved_microusd": 0,
                    "spent_microusd": 0, "expires_at": expiry.isoformat(), "created_at": now()}
            conn.execute("INSERT INTO planning_budget_grants(id,project_id,provider_kind,max_cost_microusd,reserved_microusd,spent_microusd,expires_at,created_at) VALUES(:id,:project_id,:provider_kind,:max_cost_microusd,:reserved_microusd,:spent_microusd,:expires_at,:created_at)", item)
            self.studio.audit(conn, project_id, "planning.budget_granted", item["id"], {"provider": provider_kind, "max_cost_microusd": max_cost_microusd})
            return item

    def grants(self, project_id: str) -> list[dict]:
        self.studio.project(project_id)
        with self.db.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM planning_budget_grants WHERE project_id=? ORDER BY created_at DESC", (project_id,))]

    def _context(self, project_id: str) -> list[dict]:
        result = []
        allowed = ("semantic_tags", "compatible_skeletons", "source_skeleton_id", "target_skeleton_id",
                   "skeleton_id", "character_id", "duration_frames", "text")
        for asset in self.studio.registry(project_id):
            result.append({"registry_id": asset["registry_id"], "kind": asset["kind"], "version": asset["version"],
                           **{key: asset["metadata"][key] for key in allowed if key in asset["metadata"]}})
        return result

    def _capability_context(self) -> dict:
        path = Path(__file__).resolve().parents[3] / "docs" / "capability-profile.json"
        profile = json.loads(path.read_text(encoding="utf-8"))
        return {key: {"status": value["status"], "version": value.get("version")}
                for key, value in profile["capabilities"].items()}

    def enqueue(self, screenplay_id: str, client_request_id: str, *, fps: dict,
                grant_id: str | None = None, base_draft_id: str | None = None) -> dict:
        try:
            uuid.UUID(client_request_id)
        except (ValueError, AttributeError) as exc:
            raise DomainError("client_request_id phải là UUID.") from exc
        if not isinstance(fps, dict) or type(fps.get("num")) is not int or type(fps.get("den")) is not int or min(fps["num"], fps["den"]) <= 0:
            raise DomainError("FPS planning phải là rational dương.")
        settings = self.ai_settings.get() if not self.custom_provider else None
        provider = self.provider if self.custom_provider or settings["provider_kind"] == LocalFixturePlanner.kind else self.openai_provider
        with self.db.transaction() as conn:
            script = require(conn.execute("SELECT * FROM screenplay_versions WHERE id=?", (screenplay_id,)).fetchone(), "screenplay")
            episode = require(conn.execute("SELECT * FROM episodes WHERE id=?", (script["episode_id"],)).fetchone(), "episode")
            existing_rows = conn.execute("SELECT s.code,r.manifest_json FROM shots s JOIN shot_revisions r ON r.shot_id=s.id AND r.revision=s.current_revision WHERE s.episode_id=?", (script["episode_id"],)).fetchall()
            start = max((json.loads(row["manifest_json"])["timeline"]["out_exclusive"] for row in existing_rows), default=0)
            preferred = []
            if base_draft_id:
                base = require(conn.execute("SELECT * FROM planning_drafts WHERE id=? AND screenplay_id=?", (base_draft_id, screenplay_id)).fetchone(), "base draft")
                preferred = [shot["manifest"]["shot_id"] for shot in json.loads(base["draft_json"])["shots"]]
                if preferred:
                    first_existing = conn.execute("SELECT r.manifest_json FROM shots s JOIN shot_revisions r ON r.shot_id=s.id AND r.revision=s.current_revision WHERE s.episode_id=? AND s.code=?", (script["episode_id"], preferred[0])).fetchone()
                    if first_existing:
                        start = json.loads(first_existing[0])["timeline"]["in"]
            context = self._context(script["project_id"])
            request = {"schema_version": "p5.planning-request.1", "screenplay_id": screenplay_id,
                       "screenplay_version": script["version"], "screenplay_sha256": script["sha256"],
                       "screenplay_text": script["text"], "language": script["language"],
                       "target_language": script["target_language"], "estimated_seconds": script["estimated_seconds"],
                       "episode_name": episode["name"], "fps": fps, "start_frame": start,
                       "existing_shot_codes": [row["code"] for row in existing_rows],
                       "preferred_shot_codes": preferred, "registry_context": context,
                       "capability_context": self._capability_context(), "provider_kind": provider.kind}
            if provider.kind == "OPENAI":
                request.update({"provider_model": settings["model"], "max_output_tokens": settings["max_output_tokens"],
                                "settings_revision": settings["revision"]})
            request["input_hash"] = digest(request)
            operation_id = digest({"input_hash": request["input_hash"], "client_request_id": client_request_id})
            request["operation_id"] = operation_id
            existing = conn.execute("SELECT * FROM planning_jobs WHERE operation_id=?", (operation_id,)).fetchone()
            if existing:
                return self._job(conn, existing)
            if provider.paid:
                pending = conn.execute("SELECT * FROM planning_jobs WHERE provider_kind=? AND input_hash=? AND state IN ('QUEUED','RUNNING','RECONCILING') ORDER BY created_at DESC LIMIT 1",
                                       (provider.kind, request["input_hash"])).fetchone()
                if pending:
                    if pending["state"] == "RECONCILING":
                        raise DomainError(f"Yêu cầu trước còn cần đối soát, operation ID {pending['operation_id']}; không gửi lại có phí.", 409)
                    return self._job(conn, pending)
            estimate = provider.estimate_microusd(request)
            if type(estimate) is not int or estimate < 0:
                raise DomainError("Provider chưa có bảng ước tính chi phí planning hợp lệ.", 409)
            if provider.paid:
                if not provider.configured:
                    raise DomainError("Planner live chưa được cấu hình theo provider/model version.", 409)
                if not grant_id:
                    raise DomainError("Planning API có phí cần budget grant riêng trước request.", 409)
                grant = require(conn.execute("SELECT * FROM planning_budget_grants WHERE id=? AND project_id=? AND provider_kind=?", (grant_id, script["project_id"], provider.kind)).fetchone(), "planning grant")
                if datetime.fromisoformat(grant["expires_at"]) <= datetime.now(timezone.utc) or grant["max_cost_microusd"] - grant["reserved_microusd"] - grant["spent_microusd"] < estimate:
                    raise DomainError("Planning grant hết hạn hoặc không đủ hạn mức.", 409)
                if not self.secret_available(provider.kind):
                    raise DomainError("Planner live chưa có key trong kho bảo mật backend; không gửi request.", 409)
                budget.reserve(conn, script["project_id"], operation_id, gate=0, category="PLANNING",
                               provider_kind=provider.kind, currency="USD", amount_micro=estimate)
                conn.execute("UPDATE planning_budget_grants SET reserved_microusd=reserved_microusd+? WHERE id=?", (estimate, grant_id))
            elif grant_id:
                raise DomainError("Fixture local không sử dụng budget grant có phí.")
            timestamp = now()
            item = {"id": uid(), "project_id": script["project_id"], "episode_id": script["episode_id"],
                    "screenplay_id": screenplay_id, "provider_kind": provider.kind,
                    "operation_id": operation_id, "input_hash": request["input_hash"], "request_json": canonical(request),
                    "state": "QUEUED", "attempt": 0, "max_attempts": 1 if provider.paid else 3,
                    "estimated_cost_microusd": estimate, "grant_id": grant_id, "provider_request_id": None,
                    "error": None, "created_at": timestamp, "updated_at": timestamp}
            conn.execute("INSERT INTO planning_jobs(id,project_id,episode_id,screenplay_id,provider_kind,operation_id,input_hash,request_json,state,attempt,max_attempts,estimated_cost_microusd,grant_id,provider_request_id,error,created_at,updated_at) VALUES(:id,:project_id,:episode_id,:screenplay_id,:provider_kind,:operation_id,:input_hash,:request_json,:state,:attempt,:max_attempts,:estimated_cost_microusd,:grant_id,:provider_request_id,:error,:created_at,:updated_at)", item)
            self.studio.audit(conn, script["project_id"], "planning.queued", item["id"], {"provider": provider.kind, "estimate_microusd": estimate, "input_hash": item["input_hash"]})
            return self._job(conn, item)

    def _job(self, conn, row) -> dict:
        result = dict(row)
        result.pop("request_json", None)
        result["attempts"] = [dict(a) for a in conn.execute("SELECT * FROM planning_attempts WHERE job_id=? ORDER BY attempt", (result["id"],))]
        draft = conn.execute("SELECT id,version FROM planning_drafts WHERE job_id=?", (result["id"],)).fetchone()
        result["draft_id"] = draft["id"] if draft else None
        result["draft_version"] = draft["version"] if draft else None
        return result

    def jobs(self, project_id: str) -> list[dict]:
        self.studio.project(project_id)
        with self.db.connect() as conn:
            return [self._job(conn, row) for row in conn.execute("SELECT * FROM planning_jobs WHERE project_id=? ORDER BY created_at DESC", (project_id,))]

    def job(self, job_id: str) -> dict:
        with self.db.connect() as conn:
            return self._job(conn, require(conn.execute("SELECT * FROM planning_jobs WHERE id=?", (job_id,)).fetchone(), "planning job"))

    def _registry_for_validation(self, project_id: str) -> dict:
        with self.db.connect() as conn:
            return self.studio._registry_map(conn, project_id)

    def process_next(self) -> dict | None:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM planning_jobs WHERE state='QUEUED' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            job = dict(row)
            conn.execute("UPDATE planning_jobs SET state='RUNNING',updated_at=? WHERE id=?", (now(), job["id"]))
        request = json.loads(job["request_json"])
        provider = self.provider if self.custom_provider else (self.openai_provider if job["provider_kind"] == "OPENAI" else self.provider)
        previous_error = None
        terminal = "BLOCKED"
        for attempt in range(1, job["max_attempts"] + 1):
            attempt_id = uid()
            with self.db.transaction() as conn:
                conn.execute("INSERT INTO planning_attempts(id,job_id,attempt,state,started_at) VALUES(?,?,?,'RUNNING',?)", (attempt_id, job["id"], attempt, now()))
                conn.execute("UPDATE planning_jobs SET attempt=?,updated_at=? WHERE id=?", (attempt, now(), job["id"]))
            try:
                if provider.kind == "OPENAI":
                    current_settings = self.ai_settings.get()
                    if current_settings["revision"] != request["settings_revision"] or current_settings["provider_kind"] != "OPENAI":
                        raise PlannerNotSent("Cấu hình AI/khóa đã đổi sau khi xếp việc; yêu cầu chưa gửi.", 409)
                result = provider.generate(request, previous_error=previous_error, attempt=attempt)
                with self.db.transaction() as conn:
                    conn.execute("UPDATE planning_attempts SET provider_request_id=?,provider_model=? WHERE id=?",
                                 (result.provider_request_id, result.resolved_model, attempt_id))
                    conn.execute("UPDATE planning_jobs SET provider_request_id=? WHERE id=?", (result.provider_request_id, job["id"]))
                raw_hash = hashlib.sha256(result.raw_json.encode("utf-8")).hexdigest()
                value = parse_response(result.raw_json)
                registry = self._registry_for_validation(job["project_id"])
                validated = validate_draft(value, registry, screenplay_id=job["screenplay_id"],
                                           fps=request["fps"], existing_codes=set(request["existing_shot_codes"]),
                                           estimated_seconds=request["estimated_seconds"])
                if result.actual_cost_microusd < 0 or result.actual_cost_microusd > job["estimated_cost_microusd"]:
                    raise DomainError("Actual planning cost vượt estimate/reservation; cần đối soát.", 409)
                with self.db.transaction() as conn:
                    version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM planning_drafts WHERE screenplay_id=?", (job["screenplay_id"],)).fetchone()[0]
                    draft_id = uid()
                    conn.execute("INSERT INTO planning_drafts(id,job_id,project_id,episode_id,screenplay_id,version,draft_json,validation_json,provider_kind,provider_model,estimated_cost_microusd,actual_cost_microusd,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                 (draft_id, job["id"], job["project_id"], job["episode_id"], job["screenplay_id"], version,
                                  canonical(validated), canonical({"issues": validated["issues"], "valid_for_apply": validated["valid_for_apply"]}),
                                  provider.kind, result.resolved_model, job["estimated_cost_microusd"], result.actual_cost_microusd, now()))
                    conn.execute("UPDATE planning_attempts SET state='COMPLETED',response_sha256=?,provider_request_id=?,ended_at=? WHERE id=?",
                                 (raw_hash, result.provider_request_id, now(), attempt_id))
                    conn.execute("UPDATE planning_jobs SET state='DRAFT_READY',provider_request_id=?,error=NULL,updated_at=? WHERE id=?",
                                 (result.provider_request_id, now(), job["id"]))
                    if job["grant_id"]:
                        conn.execute("UPDATE planning_budget_grants SET reserved_microusd=reserved_microusd-?,spent_microusd=spent_microusd+? WHERE id=?",
                                     (job["estimated_cost_microusd"], result.actual_cost_microusd, job["grant_id"]))
                        budget.settle(conn, job["operation_id"], result.actual_cost_microusd)
                    self.studio.audit(conn, job["project_id"], "planning.draft_ready", draft_id,
                                      {"version": version, "issues": len(validated["issues"]), "provider": provider.kind,
                                       "actual_cost_microusd": result.actual_cost_microusd})
                return self.job(job["id"])
            except Exception as exc:
                previous_error = str(exc)[:500]
                authority_violation = isinstance(exc, DomainError) and exc.status == 409
                terminal = "BLOCKED" if isinstance(exc, PlannerNotSent) else ("RECONCILING" if provider.paid else "BLOCKED")
                with self.db.transaction() as conn:
                    request_id = exc.request_id if isinstance(exc, PlannerRemoteError) else None
                    conn.execute("UPDATE planning_attempts SET state=?,validation_error=?,provider_request_id=COALESCE(?,provider_request_id),ended_at=? WHERE id=?",
                                 ("REJECTED" if authority_violation else "FAILED", previous_error, request_id, now(), attempt_id))
                    if request_id:
                        conn.execute("UPDATE planning_jobs SET provider_request_id=? WHERE id=?", (request_id, job["id"]))
                if authority_violation or provider.paid:
                    break
        with self.db.transaction() as conn:
            conn.execute("UPDATE planning_jobs SET state=?,error=?,updated_at=? WHERE id=?", (terminal, previous_error, now(), job["id"]))
            if job["grant_id"] and terminal == "BLOCKED":
                conn.execute("UPDATE planning_budget_grants SET reserved_microusd=reserved_microusd-? WHERE id=?", (job["estimated_cost_microusd"], job["grant_id"]))
                budget.release(conn, job["operation_id"])
            self.studio.audit(conn, job["project_id"], "planning." + terminal.lower(), job["id"],
                              {"attempts": attempt, "provider": provider.kind, "error_type": "validation_or_provider"})
        return self.job(job["id"])

    def drafts(self, episode_id: str) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM planning_drafts WHERE episode_id=? ORDER BY created_at DESC", (episode_id,)).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["draft"] = json.loads(item.pop("draft_json"))
                item["validation"] = json.loads(item.pop("validation_json"))
                item["applications"] = [dict(a) for a in conn.execute("SELECT * FROM planning_applications WHERE draft_id=?", (item["id"],))]
                result.append(item)
            return result

    def draft(self, draft_id: str) -> dict:
        with self.db.connect() as conn:
            row = require(conn.execute("SELECT * FROM planning_drafts WHERE id=?", (draft_id,)).fetchone(), "planning draft")
            item = dict(row)
            item["draft"] = json.loads(item.pop("draft_json"))
            item["validation"] = json.loads(item.pop("validation_json"))
            item["applications"] = [dict(a) for a in conn.execute("SELECT * FROM planning_applications WHERE draft_id=?", (draft_id,))]
            return item

    def preview_apply(self, draft_id: str, shot_code: str) -> dict:
        draft = self.draft(draft_id)
        item = next((s for s in draft["draft"]["shots"] if s["manifest"]["shot_id"] == shot_code), None)
        if not item:
            raise DomainError("Shot không nằm trong planning draft.", 404)
        manifest = item["manifest"]
        blockers = [i["message"] for i in draft["draft"]["issues"] if i["shot_id"] in (shot_code, "*") and i["kind"] != "EXISTING_SHOT"]
        if draft["draft"]["unresolved_requirements"]:
            blockers.extend(draft["draft"]["unresolved_requirements"])
        with self.db.connect() as conn:
            job = require(conn.execute("SELECT request_json FROM planning_jobs WHERE id=?", (draft["job_id"],)).fetchone(), "planning job")
            request = json.loads(job["request_json"])
            if request["registry_context"] != self._context(draft["project_id"]):
                blockers.append("Registry version đổi sau planning; cần tạo draft mới.")
            existing = conn.execute("SELECT * FROM shots WHERE project_id=? AND episode_id=? AND code=?", (draft["project_id"], draft["episode_id"], shot_code)).fetchone()
        target_revision = existing["current_revision"] if existing else 0
        current_manifest = None
        impact = {"changed_fields": ["new_shot"], "affected_subsystems": [], "stale_artifacts": [], "new_input_hash": ""}
        if existing:
            state = self.studio.shot(existing["id"])
            current_manifest = state["manifest"]
            try:
                impact = self.studio.preview_revision(existing["id"], target_revision, manifest)
            except DomainError as exc:
                blockers.append(str(exc))
            else:
                if not impact["changed_fields"]:
                    blockers.append("Draft giống manifest hiện hành; không cần apply revision mới.")
                protected = [s["subsystem"] for s in state["states"] if s["subsystem"] in impact["affected_subsystems"] and (s["mode"] == "MANUAL" or s["lock_state"] == "LOCKED")]
                if protected:
                    blockers.append("Draft ảnh hưởng nội dung MANUAL/LOCKED: " + ", ".join(protected) + "; cần quyết định thủ công, planner không ghi đè.")
        else:
            try:
                with self.db.connect() as conn:
                    registry = self.studio._registry_map(conn, draft["project_id"])
                    self.studio._siblings(conn, draft["episode_id"])
                    from .domain import validate_manifest
                    validate_manifest(manifest, registry, self.studio._siblings(conn, draft["episode_id"]))
                    impact["new_input_hash"] = digest({"manifest": manifest, "registry": self.studio._pin_registry(manifest, registry)})
            except DomainError as exc:
                blockers.append(str(exc))
        token = digest({"draft_id": draft_id, "shot_code": shot_code, "target_revision": target_revision,
                        "manifest": manifest, "impact": impact})
        return {"draft_id": draft_id, "shot_code": shot_code, "target_shot_id": existing["id"] if existing else None,
                "target_revision": target_revision, "allowed": not blockers, "blockers": list(dict.fromkeys(blockers)),
                "impact": impact, "manifest_before": current_manifest, "manifest_after": manifest,
                "preview_token": token, "requires_c1_approval": True,
                "production_jobs_enqueued": 0}

    def apply(self, draft_id: str, shot_code: str, expected_target_revision: int, preview_token: str) -> dict:
        with self.db.connect() as conn:
            previous = conn.execute("SELECT * FROM planning_applications WHERE draft_id=? AND shot_code=?", (draft_id, shot_code)).fetchone()
            if previous:
                return dict(previous)
        preview = self.preview_apply(draft_id, shot_code)
        if not preview["allowed"] or preview["target_revision"] != expected_target_revision or preview["preview_token"] != preview_token:
            raise DomainError("Planning diff/impact đã đổi hoặc còn blocker; cần xem lại trước apply.", 409)
        draft = self.draft(draft_id)
        item = next(s for s in draft["draft"]["shots"] if s["manifest"]["shot_id"] == shot_code)
        application = None
        def record_application(conn, written_shot_id: str, revision: int) -> None:
            nonlocal application
            application = {"id": uid(), "draft_id": draft_id, "shot_code": shot_code,
                           "shot_id": written_shot_id, "shot_revision": revision,
                           "applied_by": "local_user", "created_at": now()}
            conn.execute("INSERT INTO planning_applications(id,draft_id,shot_code,shot_id,shot_revision,applied_by,created_at) VALUES(:id,:draft_id,:shot_code,:shot_id,:shot_revision,:applied_by,:created_at)", application)
            self.studio.audit(conn, draft["project_id"], "planning.applied", written_shot_id,
                              {"draft_id": draft_id, "shot_revision": revision,
                               "approval_created": False, "jobs_enqueued": 0})
        if preview["target_shot_id"]:
            self.studio.revise_shot(preview["target_shot_id"], expected_target_revision, item["manifest"],
                                    require_auto_unlocked=True, expected_input_hash=preview["impact"]["new_input_hash"],
                                    after_write=record_application)
        else:
            with self.db.connect() as conn:
                ordinal = conn.execute("SELECT COALESCE(MAX(ordinal),0)+1 FROM shots WHERE episode_id=?", (draft["episode_id"],)).fetchone()[0]
            self.studio.create_shot(draft["project_id"], draft["episode_id"], ordinal, item["manifest"],
                                    expected_input_hash=preview["impact"]["new_input_hash"], after_write=record_application)
        assert application is not None
        return application
