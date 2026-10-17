"""Series library (workflow V4.5 zone 0): S1 standards, S2 entities, S3 UE asset scan, CS1 mapping approval.

Nothing here writes to Unreal. A saved mapping is an operator choice, not proof the asset works in a shot.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from . import budget, gpu_queue, workflow
from .ai_settings import AISettings, KEY_MISSING, key_available
from .domain import DomainError, canonical, digest, UE_TARGET_VERSION
from .openai_client import PlannerAnswered, PlannerNotSent, PlannerRemoteError
from .series_domain import (COLOR_SPACES, ENTITY_KINDS, MAPPABLE, VIDEO_MODELS, entity_key, fold, suggest,
                            validate_extraction, validate_scan_result)
from .claude_api import ClaudeAPIExtractor, request_fields as claude_api_fields
from .claude_code import MISSING as CLAUDE_CODE_MISSING, ClaudeCodeSettings
from .series_extractor import ClaudeCodeExtractor, FixtureExtractor, OpenAIExtractor
from .service import Studio, now, require, uid


SCAN_ROOTS = ["/Game"]
MAX_SCREENPLAY_CHARS, MAX_SCREENPLAY_SECONDS = 110_000, 7200
DEFAULT_STANDARDS = {"fps_num": 24, "fps_den": 1, "width": 1920, "height": 1080, "color_space": "Rec.709",
                     "lut_name": "", "default_video_model": "SEEDANCE_2_5"}


class SeriesService:
    def __init__(self, studio: Studio, *, scanner=None, extractor=None, secret_available=None, claude_code=None):
        self.studio = studio
        self.db = studio.db
        self.scanner = scanner or (lambda request: studio._unreal_adapter().scan_assets(request))
        self.custom_extractor = extractor
        self.claude_code = claude_code or ClaudeCodeSettings(studio)
        self.ai_settings = AISettings(studio, self.claude_code)
        self.secret_available = secret_available or key_available
        with self.db.transaction() as conn:
            # A paid request interrupted by a restart has an unknown outcome and is never resent.
            conn.execute("UPDATE series_extraction_jobs SET state=CASE WHEN provider_kind IN ('OPENAI','CLAUDE_API') THEN 'RECONCILING' ELSE 'BLOCKED' END,error='Backend khởi động lại trong lúc liệt kê.',updated_at=? WHERE state='RUNNING'", (now(),))
            conn.execute("UPDATE ue_asset_scans SET state='RECONCILING',error='Backend khởi động lại trong lúc quét; đóng Unreal cũ rồi xác nhận để quét lại.',updated_at=? WHERE state='RUNNING'", (now(),))

    # ---- S1 standards -------------------------------------------------------------------------------------------

    def standards(self, project_id: str) -> dict:
        self.studio.project(project_id)
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM series_standards WHERE project_id=?", (project_id,)).fetchone()
        value = dict(row) if row else {"project_id": project_id, "revision": 0, **DEFAULT_STANDARDS, "updated_at": None}
        # Locking these values inside the UE project is a later UE write; saved here only.
        return {**value, "saved": bool(row), "applied_to_unreal": False,
                "options": {"color_spaces": COLOR_SPACES, "video_models": VIDEO_MODELS}}

    def save_standards(self, project_id: str, expected_revision: int, fps_num: int, fps_den: int, width: int,
                       height: int, color_space: str, lut_name: str, default_video_model: str) -> dict:
        self.studio.project(project_id)
        numbers = (fps_num, fps_den, width, height)
        if any(type(v) is not int or v <= 0 for v in numbers) or fps_num > 240000 or width > 8192 or height > 8192 or width % 2 or height % 2:
            raise DomainError("FPS và độ phân giải phải là số nguyên dương; chiều rộng/cao là số chẵn, tối đa 8192.")
        if color_space not in COLOR_SPACES or default_video_model not in VIDEO_MODELS or not isinstance(lut_name, str) or len(lut_name) > 120:
            raise DomainError("Không gian màu, model video mặc định hoặc tên LUT không hợp lệ.")
        with self.db.transaction() as conn:
            row = conn.execute("SELECT revision FROM series_standards WHERE project_id=?", (project_id,)).fetchone()
            if (row["revision"] if row else 0) != expected_revision:
                raise DomainError("Chuẩn kỹ thuật đã đổi ở phiên khác; tải lại trước khi lưu.", 409)
            conn.execute("INSERT OR REPLACE INTO series_standards(project_id,revision,fps_num,fps_den,width,height,color_space,lut_name,default_video_model,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (project_id, expected_revision + 1, fps_num, fps_den, width, height, color_space, lut_name.strip(), default_video_model, now()))
            self.studio.audit(conn, project_id, "series.standards_saved", project_id, {"revision": expected_revision + 1})
        return self.standards(project_id)

    # ---- S2 screenplay and entities -----------------------------------------------------------------------------

    def ingest_screenplay(self, project_id: str, episode_id: str, text: str, language: str, estimated_seconds: int) -> dict:
        """Keep the screenplay of an episode as a new version; earlier versions are never overwritten."""
        self.studio.project(project_id)
        if not isinstance(text, str) or not 40 <= len(text) <= MAX_SCREENPLAY_CHARS or not isinstance(language, str) or not 2 <= len(language) <= 16:
            raise DomainError(f"Screenplay cần 40–{MAX_SCREENPLAY_CHARS} ký tự và mã ngôn ngữ hợp lệ.")
        if type(estimated_seconds) is not int or not 30 <= estimated_seconds <= MAX_SCREENPLAY_SECONDS:
            raise DomainError(f"Ngôn ngữ hoặc thời lượng screenplay 30–{MAX_SCREENPLAY_SECONDS} giây không hợp lệ.")
        with self.db.transaction() as conn:
            require(conn.execute("SELECT 1 FROM episodes WHERE id=? AND project_id=?", (episode_id, project_id)).fetchone(), "episode")
            version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM screenplay_versions WHERE episode_id=?", (episode_id,)).fetchone()[0]
            item = {"id": uid(), "project_id": project_id, "episode_id": episode_id, "version": version, "language": language, "target_language": "en",
                    "text": text, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "estimated_seconds": estimated_seconds, "created_at": now()}
            conn.execute("INSERT INTO screenplay_versions(id,project_id,episode_id,version,language,target_language,text,sha256,estimated_seconds,created_at) "
                         "VALUES(:id,:project_id,:episode_id,:version,:language,:target_language,:text,:sha256,:estimated_seconds,:created_at)", item)
            self.studio.audit(conn, project_id, "screenplay.ingested", item["id"], {"version": version, "sha256": item["sha256"]})
            return item

    def screenplays(self, episode_id: str) -> list[dict]:
        with self.db.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM screenplay_versions WHERE episode_id=? ORDER BY version DESC", (episode_id,))]

    def _extractor(self, settings: dict):
        if self.custom_extractor is not None:
            return self.custom_extractor
        if settings["provider_kind"] == "CLAUDE_CODE":
            return ClaudeCodeExtractor(self.claude_code)
        if settings["provider_kind"] == "CLAUDE_API":
            return ClaudeAPIExtractor()
        return OpenAIExtractor() if settings["provider_kind"] == "OPENAI" else FixtureExtractor()

    def _extraction_request(self, conn, project_id: str, screenplay_id: str) -> tuple[dict, object]:
        script = require(conn.execute("SELECT * FROM screenplay_versions WHERE id=? AND project_id=?", (screenplay_id, project_id)).fetchone(), "screenplay")
        settings = self.ai_settings.get()
        provider = self._extractor(settings)
        request = {"schema_version": "series.extraction-request.1", "project_id": project_id, "episode_id": script["episode_id"],
                   "screenplay_id": screenplay_id, "screenplay_sha256": script["sha256"], "screenplay_text": script["text"],
                   "language": script["language"], "provider_kind": provider.kind}
        if provider.kind == "OPENAI":
            request.update({"provider_model": settings["model"], "max_output_tokens": settings["max_output_tokens"],
                            "settings_revision": settings["revision"]})
        elif provider.kind == "CLAUDE_CODE":
            chosen = self.claude_code.get()
            request.update({"provider_model": chosen["model"], "settings_revision": chosen["revision"]})
        elif provider.kind == "CLAUDE_API":
            request.update(claude_api_fields(settings))
        request["input_hash"] = digest(request)
        return request, provider

    def preview_extraction(self, project_id: str, screenplay_id: str) -> dict:
        with self.db.connect() as conn:
            request, provider = self._extraction_request(conn, project_id, screenplay_id)
            estimate = provider.estimate_micro(request)
            gate = budget.check(conn, project_id, "USD", estimate) if provider.paid else {"allowed": True, "enforced": False, "reason": None}
        return {"provider_kind": provider.kind, "provider_model": request.get("provider_model"), "paid": provider.paid,
                "estimated_micro": estimate, "currency": "USD", "budget": gate,
                "note": getattr(provider, "note", None)}

    def enqueue_extraction(self, project_id: str, screenplay_id: str, client_request_id: str) -> dict:
        try:
            uuid.UUID(client_request_id)
        except (ValueError, AttributeError) as exc:
            raise DomainError("client_request_id phải là UUID.") from exc
        with self.db.transaction() as conn:
            request, provider = self._extraction_request(conn, project_id, screenplay_id)
            operation_id = digest({"input_hash": request["input_hash"], "client_request_id": client_request_id})
            request["operation_id"] = operation_id
            existing = conn.execute("SELECT * FROM series_extraction_jobs WHERE operation_id=?", (operation_id,)).fetchone()
            if existing:
                return self._extraction_job(existing)
            pending = conn.execute("SELECT * FROM series_extraction_jobs WHERE project_id=? AND state IN ('QUEUED','RUNNING','RECONCILING') ORDER BY created_at DESC LIMIT 1", (project_id,)).fetchone()
            if pending:
                if pending["state"] == "RECONCILING":
                    raise DomainError("Lần liệt kê trước chưa rõ kết quả; đóng khoản giữ trong Sổ chi phí trước khi gửi yêu cầu mới.", 409)
                return self._extraction_job(pending)
            estimate = provider.estimate_micro(request)
            if provider.kind == "CLAUDE_CODE" and not self.claude_code.available():
                raise DomainError(CLAUDE_CODE_MISSING, 409)
            if provider.paid:
                if not self.secret_available(provider.kind):
                    raise DomainError(KEY_MISSING.get(provider.kind, KEY_MISSING["OPENAI"]), 409)
                budget.reserve(conn, project_id, operation_id, gate=0, category="SERIES_EXTRACT",
                               provider_kind=provider.kind, currency="USD", amount_micro=estimate)
            stamp = now()
            item = {"id": uid(), "project_id": project_id, "episode_id": request["episode_id"], "screenplay_id": screenplay_id,
                    "provider_kind": provider.kind, "provider_model": request.get("provider_model"), "operation_id": operation_id,
                    "input_hash": request["input_hash"], "state": "QUEUED", "estimated_micro": estimate, "actual_micro": None,
                    "provider_request_id": None, "result_json": None, "error": None, "created_at": stamp, "updated_at": stamp}
            conn.execute("INSERT INTO series_extraction_jobs(id,project_id,episode_id,screenplay_id,provider_kind,provider_model,operation_id,input_hash,state,estimated_micro,actual_micro,provider_request_id,result_json,error,created_at,updated_at) VALUES(:id,:project_id,:episode_id,:screenplay_id,:provider_kind,:provider_model,:operation_id,:input_hash,:state,:estimated_micro,:actual_micro,:provider_request_id,:result_json,:error,:created_at,:updated_at)", item)
            self.studio.audit(conn, project_id, "series.extraction_queued", item["id"], {"provider": provider.kind, "estimate_micro": estimate})
            return self._extraction_job(item)

    @staticmethod
    def _extraction_job(row) -> dict:
        item = dict(row)
        item["result"] = json.loads(item.pop("result_json")) if item.get("result_json") else None
        return item

    def extraction_jobs(self, project_id: str) -> list[dict]:
        self.studio.project(project_id)
        with self.db.connect() as conn:
            return [self._extraction_job(r) for r in conn.execute("SELECT * FROM series_extraction_jobs WHERE project_id=? ORDER BY created_at DESC LIMIT 20", (project_id,))]

    def process_next_extraction(self) -> dict | None:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM series_extraction_jobs WHERE state='QUEUED' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            job = dict(row)
            conn.execute("UPDATE series_extraction_jobs SET state='RUNNING',updated_at=? WHERE id=?", (now(), job["id"]))
        sent = False
        try:
            with self.db.connect() as conn:
                request, provider = self._extraction_request(conn, job["project_id"], job["screenplay_id"])
            if request["input_hash"] != job["input_hash"]:
                raise PlannerNotSent("Kịch bản hoặc cấu hình AI đã đổi sau khi xếp việc; yêu cầu chưa gửi.", 409)
            request["operation_id"] = job["operation_id"]
            sent = provider.paid
            result = provider.extract(request)
            if result.actual_micro < 0 or result.actual_micro > job["estimated_micro"]:
                raise DomainError("Chi phí thực vượt mức đã giữ; cần đối soát.", 409)
            try:
                listing = validate_extraction(json.loads(result.raw_json))
            except (DomainError, ValueError) as exc:
                # The request was answered and billed; only its content is unusable.
                with self.db.transaction() as conn:
                    conn.execute("UPDATE series_extraction_jobs SET state='BLOCKED',actual_micro=?,provider_request_id=?,error=?,updated_at=? WHERE id=?",
                                 (result.actual_micro, result.provider_request_id, "Kết quả liệt kê không hợp lệ: " + str(exc)[:400], now(), job["id"]))
                    budget.settle(conn, job["operation_id"], result.actual_micro)
                with self.db.connect() as conn:
                    return self._extraction_job(conn.execute("SELECT * FROM series_extraction_jobs WHERE id=?", (job["id"],)).fetchone())
            with self.db.transaction() as conn:
                applied = self._apply_listing(conn, job["project_id"], listing, "AI" if provider.paid or provider.kind == "CLAUDE_CODE" else "LOCAL_FIXTURE")
                conn.execute("UPDATE series_extraction_jobs SET state='COMPLETED',actual_micro=?,provider_request_id=?,provider_model=COALESCE(?,provider_model),result_json=?,error=NULL,updated_at=? WHERE id=?",
                             (result.actual_micro, result.provider_request_id, result.resolved_model, canonical({**listing, "applied": applied}), now(), job["id"]))
                budget.settle(conn, job["operation_id"], result.actual_micro)
                self.studio.audit(conn, job["project_id"], "series.extraction_completed", job["id"], applied)
        except PlannerAnswered as exc:
            # The provider ran the request and reported its usage; only the answer is unusable.
            with self.db.transaction() as conn:
                conn.execute("UPDATE series_extraction_jobs SET state='BLOCKED',actual_micro=?,provider_request_id=COALESCE(?,provider_request_id),provider_model=COALESCE(?,provider_model),error=?,updated_at=? WHERE id=?",
                             (exc.actual_micro, exc.request_id or None, exc.model, str(exc)[:500], now(), job["id"]))
                budget.settle(conn, job["operation_id"], exc.actual_micro)
                self.studio.audit(conn, job["project_id"], "series.extraction_blocked", job["id"], {"reason": type(exc).__name__})
        except Exception as exc:
            not_sent = isinstance(exc, PlannerNotSent) or not sent
            state = "BLOCKED" if not_sent else "RECONCILING"
            with self.db.transaction() as conn:
                conn.execute("UPDATE series_extraction_jobs SET state=?,error=?,provider_request_id=COALESCE(?,provider_request_id),updated_at=? WHERE id=?",
                             (state, str(exc)[:500], exc.request_id if isinstance(exc, PlannerRemoteError) else None, now(), job["id"]))
                if not_sent:
                    budget.release(conn, job["operation_id"])
                self.studio.audit(conn, job["project_id"], "series.extraction_" + state.lower(), job["id"], {"reason": type(exc).__name__})
        with self.db.connect() as conn:
            return self._extraction_job(conn.execute("SELECT * FROM series_extraction_jobs WHERE id=?", (job["id"],)).fetchone())

    def _apply_listing(self, conn, project_id: str, listing: dict, source: str) -> dict:
        """Add to the draft list. Existing entities only gain aliases and scenes; nothing is renamed or removed."""
        created = updated = 0
        for kind, group in (("character", "characters"), ("environment", "environments")):
            rows = [dict(r) for r in conn.execute("SELECT * FROM series_entities WHERE project_id=? AND kind=? AND active=1", (project_id, kind))]
            taken = {r[0] for r in conn.execute("SELECT entity_key FROM series_entities WHERE project_id=?", (project_id,))}
            for item in listing[group]:
                names = {fold(item["name"]), *(fold(a) for a in item["aliases"])}
                match = next((r for r in rows if names & {fold(r["display_name"]), *(fold(a) for a in json.loads(r["aliases_json"]))}), None)
                if match:
                    aliases = json.loads(match["aliases_json"])
                    known = {fold(match["display_name"]), *(fold(a) for a in aliases)}
                    for alias in [item["name"], *item["aliases"]]:
                        if fold(alias) not in known:
                            aliases.append(alias)
                            known.add(fold(alias))
                    scenes = json.loads(match["scenes_json"])
                    scenes += [s for s in item["scenes"] if s not in scenes]
                    description = match["description"] or item["description"]
                    if (canonical(aliases), canonical(scenes), description) != (match["aliases_json"], match["scenes_json"], match["description"]):
                        conn.execute("UPDATE series_entities SET aliases_json=?,scenes_json=?,description=?,revision=revision+1,updated_at=? WHERE id=?",
                                     (canonical(aliases), canonical(scenes), description, now(), match["id"]))
                        match.update(aliases_json=canonical(aliases), scenes_json=canonical(scenes), description=description)
                        updated += 1
                else:
                    key = entity_key(kind, item["name"], taken)
                    taken.add(key)
                    row = {"id": uid(), "project_id": project_id, "entity_key": key, "kind": kind, "display_name": item["name"],
                           "aliases_json": canonical(item["aliases"]), "description": item["description"],
                           "scenes_json": canonical(item["scenes"]), "source": source, "revision": 1, "created_at": now(), "updated_at": now()}
                    conn.execute("INSERT INTO series_entities(id,project_id,entity_key,kind,display_name,aliases_json,description,scenes_json,source,active,merged_into,revision,created_at,updated_at) VALUES(:id,:project_id,:entity_key,:kind,:display_name,:aliases_json,:description,:scenes_json,:source,1,NULL,:revision,:created_at,:updated_at)", row)
                    rows.append(row)
                    created += 1
        return {"created": created, "updated": updated}

    @staticmethod
    def _entity(row) -> dict:
        item = dict(row)
        item["aliases"] = json.loads(item.pop("aliases_json"))
        item["scenes"] = json.loads(item.pop("scenes_json"))
        item["active"] = bool(item.get("active", 1))
        return item

    def entities(self, project_id: str) -> list[dict]:
        self.studio.project(project_id)
        with self.db.connect() as conn:
            return [self._entity(r) for r in conn.execute("SELECT * FROM series_entities WHERE project_id=? AND active=1 ORDER BY kind,display_name", (project_id,))]

    @staticmethod
    def _entity_fields(kind: str, name: str, aliases: list, description: str) -> tuple[str, list[str], str]:
        if kind not in ENTITY_KINDS or not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise DomainError("Cần loại (nhân vật/bối cảnh) và tên tối đa 80 ký tự.")
        if not isinstance(aliases, list) or len(aliases) > 20 or any(not isinstance(a, str) or len(a) > 80 for a in aliases) or not isinstance(description, str) or len(description) > 400:
            raise DomainError("Tên gọi khác tối đa 20 mục; mô tả tối đa 400 ký tự.")
        clean = []
        for alias in aliases:
            if alias.strip() and fold(alias) != fold(name) and fold(alias) not in {fold(a) for a in clean}:
                clean.append(alias.strip())
        return name.strip(), clean, description.strip()

    def _name_conflict(self, conn, project_id: str, kind: str, names: list[str], exclude_id: str | None) -> None:
        wanted = {fold(n) for n in names}
        for row in conn.execute("SELECT id,display_name,aliases_json FROM series_entities WHERE project_id=? AND kind=? AND active=1", (project_id, kind)):
            if row["id"] != exclude_id and wanted & {fold(row["display_name"]), *(fold(a) for a in json.loads(row["aliases_json"]))}:
                raise DomainError(f"Tên đã thuộc mục «{row['display_name']}»; hãy gộp hai mục thay vì tạo trùng.", 409)

    def add_entity(self, project_id: str, kind: str, name: str, aliases: list, description: str) -> dict:
        self.studio.project(project_id)
        name, aliases, description = self._entity_fields(kind, name, aliases, description)
        with self.db.transaction() as conn:
            self._name_conflict(conn, project_id, kind, [name, *aliases], None)
            taken = {r[0] for r in conn.execute("SELECT entity_key FROM series_entities WHERE project_id=?", (project_id,))}
            row = {"id": uid(), "project_id": project_id, "entity_key": entity_key(kind, name, taken), "kind": kind,
                   "display_name": name, "aliases_json": canonical(aliases), "description": description,
                   "scenes_json": "[]", "source": "MANUAL", "revision": 1, "created_at": now(), "updated_at": now()}
            conn.execute("INSERT INTO series_entities(id,project_id,entity_key,kind,display_name,aliases_json,description,scenes_json,source,active,merged_into,revision,created_at,updated_at) VALUES(:id,:project_id,:entity_key,:kind,:display_name,:aliases_json,:description,:scenes_json,:source,1,NULL,:revision,:created_at,:updated_at)", row)
            self.studio.audit(conn, project_id, "series.entity_added", row["id"], {"key": row["entity_key"]})
            return self._entity(row)

    def _active_entity(self, conn, entity_id: str, expected_revision: int) -> dict:
        row = require(conn.execute("SELECT * FROM series_entities WHERE id=? AND active=1", (entity_id,)).fetchone(), "entity")
        if row["revision"] != expected_revision:
            raise DomainError("Mục này đã đổi ở phiên khác; tải lại trước khi sửa.", 409)
        return row

    def update_entity(self, entity_id: str, expected_revision: int, name: str, aliases: list, description: str) -> dict:
        with self.db.transaction() as conn:
            row = self._active_entity(conn, entity_id, expected_revision)
            name, aliases, description = self._entity_fields(row["kind"], name, aliases, description)
            self._name_conflict(conn, row["project_id"], row["kind"], [name, *aliases], entity_id)
            # entity_key is the stable ID and never follows a rename.
            conn.execute("UPDATE series_entities SET display_name=?,aliases_json=?,description=?,revision=revision+1,updated_at=? WHERE id=?",
                         (name, canonical(aliases), description, now(), entity_id))
            self.studio.audit(conn, row["project_id"], "series.entity_updated", entity_id, {"key": row["entity_key"]})
            return self._entity(conn.execute("SELECT * FROM series_entities WHERE id=?", (entity_id,)).fetchone())

    def merge_entities(self, source_id: str, source_revision: int, target_id: str, target_revision: int) -> dict:
        if source_id == target_id:
            raise DomainError("Chọn hai mục khác nhau để gộp.")
        with self.db.transaction() as conn:
            source = self._active_entity(conn, source_id, source_revision)
            target = self._active_entity(conn, target_id, target_revision)
            if source["kind"] != target["kind"] or source["project_id"] != target["project_id"]:
                raise DomainError("Chỉ gộp hai mục cùng loại trong cùng dự án.")
            aliases = json.loads(target["aliases_json"])
            known = {fold(target["display_name"]), *(fold(a) for a in aliases)}
            for alias in [source["display_name"], *json.loads(source["aliases_json"])]:
                if fold(alias) not in known:
                    aliases.append(alias)
                    known.add(fold(alias))
            scenes = json.loads(target["scenes_json"])
            scenes += [s for s in json.loads(source["scenes_json"]) if s not in scenes]
            conn.execute("UPDATE series_entities SET aliases_json=?,scenes_json=?,description=?,revision=revision+1,updated_at=? WHERE id=?",
                         (canonical(aliases), canonical(scenes), target["description"] or source["description"], now(), target_id))
            conn.execute("UPDATE series_entities SET active=0,merged_into=?,revision=revision+1,updated_at=? WHERE id=?", (target_id, now(), source_id))
            conn.execute("DELETE FROM series_mappings WHERE entity_id=?", (source_id,))
            self.studio.audit(conn, target["project_id"], "series.entity_merged", target_id, {"from": source["entity_key"], "into": target["entity_key"]})
            return self._entity(conn.execute("SELECT * FROM series_entities WHERE id=?", (target_id,)).fetchone())

    def remove_entity(self, entity_id: str, expected_revision: int) -> dict:
        with self.db.transaction() as conn:
            row = self._active_entity(conn, entity_id, expected_revision)
            conn.execute("UPDATE series_entities SET active=0,revision=revision+1,updated_at=? WHERE id=?", (now(), entity_id))
            conn.execute("DELETE FROM series_mappings WHERE entity_id=?", (entity_id,))
            self.studio.audit(conn, row["project_id"], "series.entity_removed", entity_id, {"key": row["entity_key"]})
        return {"removed": entity_id}

    # ---- S3 UE asset scan ---------------------------------------------------------------------------------------

    def enqueue_scan(self, project_id: str) -> dict:
        self.studio.project(project_id)
        self.studio._unreal_adapter()  # Fail before enqueue if Unreal is not configured.
        _, project_path, _ = self.studio._unreal_paths()
        with self.db.transaction() as conn:
            active = conn.execute("SELECT * FROM ue_asset_scans WHERE state IN ('QUEUED','RUNNING','RECONCILING') ORDER BY created_at DESC LIMIT 1").fetchone()
            if active:
                if active["state"] == "RECONCILING":
                    raise DomainError("Lần quét trước bị gián đoạn; đóng Unreal cũ và xác nhận trong mục S3 trước khi quét lại.", 409)
                if active["project_id"] != project_id:
                    raise DomainError("Đang có lần quét Unreal của dự án khác; chờ hoàn tất.", 409)
                return dict(active)
            stamp = now()
            item = {"id": uid(), "project_id": project_id, "operation_id": digest({"scan": uid(), "project": project_id}),
                    "state": "QUEUED", "ue_project_path": str(Path(project_path).resolve()), "engine_version": None,
                    "asset_count": None, "inventory_sha256": None, "error": None, "created_at": stamp, "updated_at": stamp}
            conn.execute("INSERT INTO ue_asset_scans(id,project_id,operation_id,state,ue_project_path,engine_version,asset_count,inventory_sha256,error,created_at,updated_at) VALUES(:id,:project_id,:operation_id,:state,:ue_project_path,:engine_version,:asset_count,:inventory_sha256,:error,:created_at,:updated_at)", item)
            self.studio.audit(conn, project_id, "series.scan_queued", item["id"], {"operation_id": item["operation_id"]})
            return item

    def process_next_scan(self) -> dict | None:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM ue_asset_scans WHERE state='QUEUED' ORDER BY created_at LIMIT 1").fetchone()
            if not row or gpu_queue.holder(conn):
                return None
            scan = dict(row)
            gpu_queue.try_acquire(conn, scan["operation_id"], "SCAN")
            conn.execute("UPDATE ue_asset_scans SET state='RUNNING',updated_at=? WHERE id=?", (now(), scan["id"]))
        try:
            _, project_path, _ = self.studio._unreal_paths()
            if not project_path or str(Path(project_path).resolve()) != scan["ue_project_path"]:
                raise DomainError("Project Unreal trong Cài đặt đã đổi sau khi xếp việc quét; quét lại cho project mới.", 409)
            result = self.scanner({"schema_version": "series.scan-request.1", "operation_id": scan["operation_id"], "roots": SCAN_ROOTS})
            version, assets = validate_scan_result(result, scan["operation_id"], UE_TARGET_VERSION)
            with self.db.transaction() as conn:
                conn.executemany("INSERT INTO ue_scan_assets(scan_id,object_path,asset_name,package_path,asset_class,category,skeleton_path,parent_class) VALUES(?,?,?,?,?,?,?,?)",
                                 [(scan["id"], a["object_path"], a["asset_name"], a["package_path"], a["asset_class"], a["category"], a["skeleton_path"], a["parent_class"]) for a in assets])
                conn.execute("UPDATE ue_asset_scans SET state='COMPLETED',engine_version=?,asset_count=?,inventory_sha256=?,error=NULL,updated_at=? WHERE id=?",
                             (version, len(assets), digest(assets), now(), scan["id"]))
                gpu_queue.release(conn, scan["operation_id"])
                self.studio.audit(conn, scan["project_id"], "series.scan_completed", scan["id"], {"assets": len(assets), "engine": version})
        except Exception as exc:
            # The scan is read-only and the editor process has exited or been killed by the timeout, so the GPU is free.
            with self.db.transaction() as conn:
                conn.execute("UPDATE ue_asset_scans SET state='BLOCKED',error=?,updated_at=? WHERE id=?",
                             (str(exc)[:500] if isinstance(exc, DomainError) else f"{type(exc).__name__}: không quét được asset Unreal.", now(), scan["id"]))
                gpu_queue.release(conn, scan["operation_id"])
                self.studio.audit(conn, scan["project_id"], "series.scan_blocked", scan["id"], {"reason": type(exc).__name__})
        return self.scan(scan["id"])

    def release_interrupted_scan(self, scan_id: str, worker_stopped: bool) -> dict:
        with self.db.transaction() as conn:
            scan = require(conn.execute("SELECT * FROM ue_asset_scans WHERE id=?", (scan_id,)).fetchone(), "scan")
            if scan["state"] != "RECONCILING":
                raise DomainError("Lần quét này không ở trạng thái cần xác nhận.", 409)
            if not worker_stopped:
                raise DomainError("Đóng cửa sổ Unreal của lần quét cũ rồi xác nhận trong giao diện.", 409)
            conn.execute("UPDATE ue_asset_scans SET state='BLOCKED',error='Bị gián đoạn; chưa có kết quả. Quét lại khi cần.',updated_at=? WHERE id=?", (now(), scan_id))
            gpu_queue.release(conn, scan["operation_id"])
            self.studio.audit(conn, scan["project_id"], "series.scan_released", scan_id, {"operator_confirmed_stopped": True})
        return self.scan(scan_id)

    def scan(self, scan_id: str) -> dict:
        with self.db.connect() as conn:
            return self._scan(conn, require(conn.execute("SELECT * FROM ue_asset_scans WHERE id=?", (scan_id,)).fetchone(), "scan"))

    @staticmethod
    def _scan(conn, row) -> dict:
        item = dict(row)
        item["categories"] = {r[0]: r[1] for r in conn.execute("SELECT category,COUNT(*) FROM ue_scan_assets WHERE scan_id=? GROUP BY category", (item["id"],))}
        return item

    def scans(self, project_id: str) -> dict:
        self.studio.project(project_id)
        _, project_path, _ = self.studio._unreal_paths()
        current_path = str(Path(project_path).resolve()) if project_path else ""
        with self.db.connect() as conn:
            rows = [self._scan(conn, r) for r in conn.execute("SELECT * FROM ue_asset_scans WHERE project_id=? ORDER BY created_at DESC LIMIT 10", (project_id,))]
            latest = self._latest_scan(conn, project_id)
        return {"scans": rows, "latest_completed_id": latest["id"] if latest else None, "current_ue_project_path": current_path,
                "latest_matches_current_project": bool(latest and latest["ue_project_path"] == current_path),
                "target_engine": UE_TARGET_VERSION, "thumbnails": "NOT_AVAILABLE"}

    @staticmethod
    def _latest_scan(conn, project_id: str):
        return conn.execute("SELECT * FROM ue_asset_scans WHERE project_id=? AND state='COMPLETED' ORDER BY created_at DESC LIMIT 1", (project_id,)).fetchone()

    def scan_assets(self, project_id: str, category: str | None = None, q: str = "", folder: str = "",
                    limit: int = 100, offset: int = 0) -> dict:
        self.studio.project(project_id)
        limit, offset = max(1, min(limit, 500)), max(0, offset)
        with self.db.connect() as conn:
            scan = self._latest_scan(conn, project_id)
            if not scan:
                return {"scan_id": None, "total": 0, "assets": []}
            where, values = ["scan_id=?"], [scan["id"]]
            if category:
                where.append("category IN (" + ",".join("?" for _ in category.split(",")) + ")")
                values += category.split(",")
            if folder:
                where.append("(package_path=? OR package_path LIKE ? ESCAPE '\\')")
                values += [folder, folder.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"]
            if q.strip():
                where.append("asset_name LIKE ? ESCAPE '\\'")
                values.append("%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
            clause = " AND ".join(where)
            total = conn.execute(f"SELECT COUNT(*) FROM ue_scan_assets WHERE {clause}", values).fetchone()[0]
            rows = conn.execute(f"SELECT object_path,asset_name,package_path,asset_class,category,skeleton_path FROM ue_scan_assets WHERE {clause} ORDER BY object_path LIMIT ? OFFSET ?", (*values, limit, offset)).fetchall()
        return {"scan_id": scan["id"], "total": total, "assets": [dict(r) for r in rows]}

    def scan_folders(self, project_id: str, category: str) -> list[dict]:
        """Folder tree with counts including sub-folders, for picking animation and VFX folders."""
        self.studio.project(project_id)
        if category not in ("animation", "niagara"):
            raise DomainError("Chỉ duyệt cây thư mục animation hoặc VFX.")
        with self.db.connect() as conn:
            scan = self._latest_scan(conn, project_id)
            rows = conn.execute("SELECT package_path,COUNT(*) FROM ue_scan_assets WHERE scan_id=? AND category=? GROUP BY package_path", (scan["id"], category)).fetchall() if scan else []
        totals: dict[str, int] = {}
        for path, count in rows:
            parts = path.strip("/").split("/")
            for depth in range(1, len(parts) + 1):
                key = "/" + "/".join(parts[:depth])
                totals[key] = totals.get(key, 0) + count
        return [{"path": path, "count": count, "depth": path.count("/") - 1} for path, count in sorted(totals.items())]

    # ---- CS1 mapping --------------------------------------------------------------------------------------------

    def _draft(self, conn, project_id: str) -> dict:
        row = conn.execute("SELECT * FROM series_library_drafts WHERE project_id=?", (project_id,)).fetchone()
        if not row:
            return {"revision": 0, "animation_folders": [], "vfx_folders": [], "retargeters": []}
        return {"revision": row["revision"], "animation_folders": json.loads(row["animation_folders_json"]),
                "vfx_folders": json.loads(row["vfx_folders_json"]), "retargeters": json.loads(row["retargeters_json"])}

    def set_mapping(self, entity_id: str, expected_revision: int, status: str, asset_path: str | None = None,
                    body_mesh_path: str | None = None, face_mesh_path: str | None = None, note: str = "") -> dict:
        if status not in ("MAPPED", "MISSING") or not isinstance(note, str) or len(note) > 300:
            raise DomainError("Trạng thái mapping phải là Đã chọn asset hoặc Thiếu; ghi chú tối đa 300 ký tự.")
        with self.db.transaction() as conn:
            entity = require(conn.execute("SELECT * FROM series_entities WHERE id=? AND active=1", (entity_id,)).fetchone(), "entity")
            current = conn.execute("SELECT revision FROM series_mappings WHERE entity_id=?", (entity_id,)).fetchone()
            if (current["revision"] if current else 0) != expected_revision:
                raise DomainError("Mapping đã đổi ở phiên khác; tải lại trước khi lưu.", 409)
            scan = self._latest_scan(conn, entity["project_id"])
            if status == "MISSING":
                asset_path = body_mesh_path = face_mesh_path = None
            else:
                if not scan:
                    raise DomainError("Chưa có lần quét asset Unreal hoàn tất; chạy S3 trước khi chọn mapping.", 409)
                self._require_asset(conn, scan["id"], asset_path, MAPPABLE[entity["kind"]], "asset")
                if entity["kind"] == "environment":
                    body_mesh_path = face_mesh_path = None
                for path, label in ((body_mesh_path, "Body mesh"), (face_mesh_path, "Face mesh")):
                    if path:
                        self._require_asset(conn, scan["id"], path, ("skeletal_mesh",), label)
            conn.execute("INSERT OR REPLACE INTO series_mappings(entity_id,project_id,status,scan_id,asset_path,body_mesh_path,face_mesh_path,note,revision,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (entity_id, entity["project_id"], status, scan["id"] if scan else None, asset_path, body_mesh_path, face_mesh_path, note.strip(), expected_revision + 1, now()))
            self.studio.audit(conn, entity["project_id"], "series.mapping_saved", entity_id, {"key": entity["entity_key"], "status": status})
        return self.mapping(entity["project_id"])

    @staticmethod
    def _require_asset(conn, scan_id: str, path: object, categories: tuple, label: str) -> dict:
        row = conn.execute("SELECT * FROM ue_scan_assets WHERE scan_id=? AND object_path=?", (scan_id, path)).fetchone() if isinstance(path, str) and path else None
        if row is None or row["category"] not in categories:
            raise DomainError(f"{label}: chọn một asset đúng loại trong danh sách đã quét, không nhập đường dẫn tự do.", 409)
        return dict(row)

    def save_folders(self, project_id: str, expected_revision: int, animation_folders: list, vfx_folders: list, retargeters: list) -> dict:
        self.studio.project(project_id)
        with self.db.transaction() as conn:
            draft = self._draft(conn, project_id)
            if draft["revision"] != expected_revision:
                raise DomainError("Lựa chọn thư mục đã đổi ở phiên khác; tải lại trước khi lưu.", 409)
            scan = self._latest_scan(conn, project_id)
            if not scan:
                raise DomainError("Chưa có lần quét asset Unreal hoàn tất.", 409)
            for folders, category in ((animation_folders, "animation"), (vfx_folders, "niagara")):
                if not isinstance(folders, list) or len(folders) > 200 or len(set(folders)) != len(folders):
                    raise DomainError("Danh sách thư mục không hợp lệ hoặc trùng.")
                for folder in folders:
                    found = isinstance(folder, str) and conn.execute("SELECT 1 FROM ue_scan_assets WHERE scan_id=? AND category=? AND (package_path=? OR package_path LIKE ? ESCAPE '\\') LIMIT 1",
                                                                    (scan["id"], category, folder, folder.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%")).fetchone()
                    if not found:
                        raise DomainError(f"Thư mục {folder!r} không có asset phù hợp trong lần quét mới nhất.", 409)
            keys = {r[0] for r in conn.execute("SELECT entity_key FROM series_entities WHERE project_id=? AND kind='character' AND active=1", (project_id,))}
            clean, seen = [], set()
            if not isinstance(retargeters, list) or len(retargeters) > 2000:
                raise DomainError("Danh sách retarget không hợp lệ.")
            for item in retargeters:
                if not isinstance(item, dict) or set(item) != {"source_skeleton", "target_entity_key", "mode", "retargeter_path"} or item["mode"] not in ("EXISTING", "NEEDS_CREATE"):
                    raise DomainError("Mỗi quyết định retarget cần skeleton nguồn, nhân vật đích và cách xử lý.")
                pair = (item["source_skeleton"], item["target_entity_key"])
                if not isinstance(item["source_skeleton"], str) or item["target_entity_key"] not in keys or pair in seen:
                    raise DomainError("Quyết định retarget trùng hoặc trỏ tới nhân vật không còn trong danh sách.", 409)
                seen.add(pair)
                path = None
                if item["mode"] == "EXISTING":
                    path = self._require_asset(conn, scan["id"], item["retargeter_path"], ("ik_retargeter",), "IK Retargeter")["object_path"]
                clean.append({"source_skeleton": item["source_skeleton"], "target_entity_key": item["target_entity_key"],
                              "mode": item["mode"], "retargeter_path": path})
            conn.execute("INSERT OR REPLACE INTO series_library_drafts(project_id,revision,animation_folders_json,vfx_folders_json,retargeters_json,updated_at) VALUES(?,?,?,?,?,?)",
                         (project_id, expected_revision + 1, canonical(sorted(animation_folders)), canonical(sorted(vfx_folders)), canonical(clean), now()))
            self.studio.audit(conn, project_id, "series.folders_saved", project_id, {"revision": expected_revision + 1})
        return self.mapping(project_id)

    def _retarget_pairs(self, conn, scan, draft: dict, characters: list[dict]) -> list[dict]:
        if not scan or not draft["animation_folders"]:
            return []
        sources: dict[str, int] = {}
        for folder in draft["animation_folders"]:
            like = folder.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"
            for skeleton, count in conn.execute("SELECT COALESCE(skeleton_path,''),COUNT(*) FROM ue_scan_assets WHERE scan_id=? AND category='animation' AND (package_path=? OR package_path LIKE ? ESCAPE '\\') GROUP BY skeleton_path", (scan["id"], folder, like)):
                sources[skeleton] = sources.get(skeleton, 0) + count
        decisions = {(d["source_skeleton"], d["target_entity_key"]): d for d in draft["retargeters"]}
        pairs = []
        for character in characters:
            mapping = character["mapping"]
            if not mapping or mapping["status"] != "MAPPED" or not mapping["body_mesh_path"]:
                continue
            mesh = conn.execute("SELECT skeleton_path FROM ue_scan_assets WHERE scan_id=? AND object_path=?", (scan["id"], mapping["body_mesh_path"])).fetchone()
            target = mesh["skeleton_path"] if mesh else None
            for skeleton, count in sorted(sources.items()):
                decision = decisions.get((skeleton, character["entity_key"]))
                direct = bool(skeleton) and skeleton == target
                pairs.append({"source_skeleton": skeleton, "animation_count": count, "target_entity_key": character["entity_key"],
                              "target_skeleton": target, "mode": "DIRECT" if direct else (decision["mode"] if decision else "UNDECIDED"),
                              "retargeter_path": None if direct or not decision else decision["retargeter_path"]})
        return pairs

    def _state(self, conn, project_id: str) -> dict:
        scan = self._latest_scan(conn, project_id)
        draft = self._draft(conn, project_id)
        entities = []
        for row in conn.execute("SELECT * FROM series_entities WHERE project_id=? AND active=1 ORDER BY kind,display_name", (project_id,)):
            entity = self._entity(row)
            mapping = conn.execute("SELECT * FROM series_mappings WHERE entity_id=?", (entity["id"],)).fetchone()
            entity["mapping"] = dict(mapping) if mapping else None
            entities.append(entity)
        pairs = self._retarget_pairs(conn, scan, draft, [e for e in entities if e["kind"] == "character"])
        return {"scan": dict(scan) if scan else None, "draft": draft, "entities": entities, "retarget_pairs": pairs}

    def _blockers(self, conn, project_id: str, state: dict) -> list[str]:
        blockers = []
        if not conn.execute("SELECT 1 FROM series_standards WHERE project_id=?", (project_id,)).fetchone():
            blockers.append("S1: chưa lưu chuẩn kỹ thuật của series.")
        if not state["entities"]:
            blockers.append("S2: chưa có nhân vật hoặc bối cảnh nào trong danh sách.")
        scan = state["scan"]
        _, project_path, _ = self.studio._unreal_paths()
        if not scan:
            blockers.append("S3: chưa có lần quét asset Unreal hoàn tất.")
        elif not project_path or scan["ue_project_path"] != str(Path(project_path).resolve()):
            blockers.append("S3: lần quét mới nhất thuộc project Unreal khác với project đang cấu hình; quét lại.")
        for entity in state["entities"]:
            mapping = entity["mapping"]
            if not mapping:
                blockers.append(f"{entity['display_name']}: chưa chọn asset hoặc đánh dấu Thiếu.")
            elif mapping["status"] == "MAPPED" and scan:
                paths = [mapping["asset_path"], mapping["body_mesh_path"], mapping["face_mesh_path"]]
                if any(p and not conn.execute("SELECT 1 FROM ue_scan_assets WHERE scan_id=? AND object_path=?", (scan["id"], p)).fetchone() for p in paths):
                    blockers.append(f"{entity['display_name']}: asset đã chọn không còn trong lần quét mới nhất; chọn lại.")
                elif entity["kind"] == "character" and not mapping["body_mesh_path"]:
                    blockers.append(f"{entity['display_name']}: chọn Body mesh để xác định skeleton đích cho retarget.")
        for pair in state["retarget_pairs"]:
            if pair["mode"] == "UNDECIDED":
                blockers.append(f"Retarget {pair['source_skeleton'] or 'skeleton không rõ'} → {pair['target_entity_key']}: chưa chọn retargeter hoặc đánh dấu cần tạo.")
        return blockers

    def _snapshot(self, conn, project_id: str, state: dict) -> dict:
        standards = conn.execute("SELECT * FROM series_standards WHERE project_id=?", (project_id,)).fetchone()
        scan = state["scan"]
        return {"schema_version": "series.library.1",
                "standards": dict(standards) if standards else None,
                "scan": {"inventory_sha256": scan["inventory_sha256"], "engine_version": scan["engine_version"],
                         "ue_project_path": scan["ue_project_path"]} if scan else None,
                "entities": [{"entity_key": e["entity_key"], "kind": e["kind"], "display_name": e["display_name"], "aliases": e["aliases"],
                              "description": e["description"], "scenes": e["scenes"],
                              "mapping": {k: e["mapping"][k] for k in ("status", "asset_path", "body_mesh_path", "face_mesh_path", "note")} if e["mapping"] else None}
                             for e in state["entities"]],
                "animation_folders": state["draft"]["animation_folders"], "vfx_folders": state["draft"]["vfx_folders"],
                "retarget_pairs": state["retarget_pairs"]}

    def mapping(self, project_id: str) -> dict:
        self.studio.project(project_id)
        with self.db.connect() as conn:
            state = self._state(conn, project_id)
            scan = state["scan"]
            for entity in state["entities"]:
                candidates = [dict(r) for r in conn.execute("SELECT object_path,asset_name,category FROM ue_scan_assets WHERE scan_id=? AND category IN (" + ",".join("?" for _ in MAPPABLE[entity["kind"]]) + ")",
                                                            (scan["id"], *MAPPABLE[entity["kind"]]))] if scan else []
                entity["suggestions"] = suggest([entity["display_name"], *entity["aliases"]], candidates)
            blockers = self._blockers(conn, project_id, state)
            snapshot_hash = digest(self._snapshot(conn, project_id, state))
            approved = conn.execute("SELECT * FROM series_library_versions WHERE project_id=? ORDER BY version DESC LIMIT 1", (project_id,)).fetchone()
            retargeters = [dict(r) for r in conn.execute("SELECT object_path,asset_name FROM ue_scan_assets WHERE scan_id=? AND category='ik_retargeter' ORDER BY asset_name", (scan["id"],))] if scan else []
        return {**state, "retargeter_assets": retargeters, "blockers": blockers, "allowed": not blockers, "draft_hash": snapshot_hash,
                "library": {"version": approved["version"], "snapshot_hash": approved["snapshot_hash"], "created_at": approved["created_at"],
                            "draft_changed": approved["snapshot_hash"] != snapshot_hash} if approved else None,
                "pending_unreal_work": sum(1 for p in state["retarget_pairs"] if p["mode"] == "NEEDS_CREATE")}

    def approve_library(self, conn, project_id: str, expected_draft_hash: str | None = None) -> int:
        """Register the current draft as the next library version (CS1, and C2 when an episode adds resources)."""
        state = self._state(conn, project_id)
        blockers = self._blockers(conn, project_id, state)
        if blockers:
            raise DomainError("Chưa duyệt được mapping thư viện (CS1): " + " · ".join(blockers[:6]), 409)
        snapshot = self._snapshot(conn, project_id, state)
        snapshot_hash = digest(snapshot)
        if expected_draft_hash is not None and snapshot_hash != expected_draft_hash:
            raise DomainError("Danh sách hoặc mapping đã đổi sau khi bạn xem; tải lại rồi duyệt đúng bản hiện tại.", 409)
        latest = conn.execute("SELECT version,snapshot_hash FROM series_library_versions WHERE project_id=? ORDER BY version DESC LIMIT 1", (project_id,)).fetchone()
        if latest and latest["snapshot_hash"] == snapshot_hash:
            raise DomainError("Bản này đã được duyệt; không có thay đổi để tăng version thư viện.", 409)
        version = (latest["version"] if latest else 0) + 1
        conn.execute("INSERT INTO series_library_versions(id,project_id,version,snapshot_json,snapshot_hash,scan_id,created_at) VALUES(?,?,?,?,?,?,?)",
                     (uid(), project_id, version, canonical(snapshot), snapshot_hash, state["scan"]["id"], now()))
        workflow.record_checkpoint(self.studio, conn, project_id, "CS1", project_id, version, snapshot_hash,
                                   {"entities": len(state["entities"]),
                                    "missing": sum(1 for e in state["entities"] if e["mapping"]["status"] == "MISSING"),
                                    "retargeters_to_create": sum(1 for p in state["retarget_pairs"] if p["mode"] == "NEEDS_CREATE")})
        return version

    def approve_cs1(self, project_id: str, expected_draft_hash: str) -> dict:
        self.studio.project(project_id)
        with self.db.transaction() as conn:
            self.approve_library(conn, project_id, expected_draft_hash)
        return self.mapping(project_id)

    def library_state(self, conn, project_id: str) -> dict:
        """Approved library version and whether the draft has moved past it."""
        state = self._state(conn, project_id)
        approved = conn.execute("SELECT * FROM series_library_versions WHERE project_id=? ORDER BY version DESC LIMIT 1", (project_id,)).fetchone()
        draft_hash = digest(self._snapshot(conn, project_id, state))
        return {"state": state, "draft_hash": draft_hash, "blockers": self._blockers(conn, project_id, state),
                "version": approved["version"] if approved else 0, "snapshot_hash": approved["snapshot_hash"] if approved else None,
                "snapshot": json.loads(approved["snapshot_json"]) if approved else None,
                "draft_changed": bool(approved) and approved["snapshot_hash"] != draft_hash}

    def scan_diff(self, project_id: str) -> dict:
        """Assets added or removed between the two most recent completed scans (workflow step 01)."""
        self.studio.project(project_id)
        with self.db.connect() as conn:
            scans = conn.execute("SELECT id,created_at FROM ue_asset_scans WHERE project_id=? AND state='COMPLETED' ORDER BY created_at DESC LIMIT 2", (project_id,)).fetchall()
            if len(scans) < 2:
                return {"compared": False, "added": [], "added_count": 0, "removed_count": 0}
            new, old = scans[0]["id"], scans[1]["id"]
            added = conn.execute("SELECT object_path,asset_name,category FROM ue_scan_assets WHERE scan_id=? AND object_path NOT IN (SELECT object_path FROM ue_scan_assets WHERE scan_id=?) ORDER BY object_path", (new, old)).fetchall()
            removed = conn.execute("SELECT COUNT(*) FROM ue_scan_assets WHERE scan_id=? AND object_path NOT IN (SELECT object_path FROM ue_scan_assets WHERE scan_id=?)", (old, new)).fetchone()[0]
        return {"compared": True, "previous_scan_at": scans[1]["created_at"], "added": [dict(r) for r in added[:50]],
                "added_count": len(added), "removed_count": removed}

    def library_versions(self, project_id: str) -> list[dict]:
        self.studio.project(project_id)
        with self.db.connect() as conn:
            return [{"version": r["version"], "snapshot_hash": r["snapshot_hash"], "created_at": r["created_at"],
                     "snapshot": json.loads(r["snapshot_json"])} for r in conn.execute("SELECT * FROM series_library_versions WHERE project_id=? ORDER BY version DESC", (project_id,))]
