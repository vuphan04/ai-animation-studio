"""Episode content for workflow V4.5 zone A: open (01), breakdown (03), C1, library comparison (04) and C2.

Nothing here creates shots for execution, routes or production jobs; routes are decided in route_service (C3)
and dialogue timing in timing_service (C4).
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from pathlib import Path

from . import budget, workflow
from .ai_settings import AISettings
from .breakdown_domain import (SCHEMA, apply_scene_edit, assign_keys, clean_draft, clean_scene, entity_index, merge_draft,
                               scene_hash, scene_review, tag_usage, usage)
from .breakdown_provider import BreakdownTruncated, ClaudeCodeBreakdown, FixtureBreakdown, OpenAIBreakdown
from .claude_code import MISSING as CLAUDE_CODE_MISSING, ClaudeCodeSettings
from .domain import DomainError, canonical, digest, UE_TARGET_VERSION
from .openai_client import PlannerNotSent, PlannerRemoteError
from .secrets import get_provider_key
from .service import now, require, uid


class BreakdownService:
    def __init__(self, studio, series, style, *, provider=None, secret_available=None, claude_code=None):
        self.studio = studio
        self.db = studio.db
        self.series = series
        self.style = style
        self.custom_provider = provider
        self.claude_code = claude_code or ClaudeCodeSettings(studio)
        self.ai_settings = AISettings(studio, self.claude_code)
        self.secret_available = secret_available or (lambda: bool(get_provider_key("OPENAI")))
        with self.db.transaction() as conn:
            # A paid request interrupted by a restart has an unknown outcome and is never resent.
            conn.execute("UPDATE breakdown_jobs SET state=CASE WHEN provider_kind='OPENAI' THEN 'RECONCILING' ELSE 'BLOCKED' END,error='Backend khởi động lại trong lúc phân tích.',updated_at=? WHERE state='RUNNING'", (now(),))

    # ---- shared lookups ------------------------------------------------------------------------------------------

    @staticmethod
    def _episode(conn, episode_id: str) -> dict:
        return dict(require(conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone(), "episode"))

    @staticmethod
    def _library(conn, project_id: str) -> dict:
        """Approved library and Style bible versions; a breakdown is always written against these."""
        library = conn.execute("SELECT version,snapshot_hash FROM series_library_versions WHERE project_id=? ORDER BY version DESC LIMIT 1", (project_id,)).fetchone()
        bible = conn.execute("SELECT version,snapshot_hash,snapshot_json FROM style_bible_versions WHERE project_id=? ORDER BY version DESC LIMIT 1", (project_id,)).fetchone()
        style = json.loads(bible["snapshot_json"]) if bible else {"style_phrases": [], "banned_words": []}
        blockers = []
        if not library:
            blockers.append("CS1: thư viện series chưa được duyệt.")
        if not bible:
            blockers.append("CS2: Style bible chưa được duyệt.")
        return {"library_version": library["version"] if library else 0, "library_hash": library["snapshot_hash"] if library else None,
                "style_version": bible["version"] if bible else 0, "style_hash": bible["snapshot_hash"] if bible else None,
                "style_phrases": style["style_phrases"], "banned_words": style["banned_words"], "blockers": blockers}

    @staticmethod
    def _entities(conn, project_id: str) -> list[dict]:
        return [{"id": r["id"], "entity_key": r["entity_key"], "kind": r["kind"], "display_name": r["display_name"],
                 "aliases": json.loads(r["aliases_json"]), "description": r["description"], "revision": r["revision"]}
                for r in conn.execute("SELECT * FROM series_entities WHERE project_id=? AND active=1 ORDER BY kind,display_name", (project_id,))]

    @staticmethod
    def _revision(conn, episode_id: str):
        return conn.execute("SELECT * FROM breakdown_revisions WHERE episode_id=? ORDER BY version DESC LIMIT 1", (episode_id,)).fetchone()

    @staticmethod
    def _approved_scenes(conn, episode_id: str, content: dict) -> set[str]:
        approvals = {(r[0], r[1]) for r in conn.execute("SELECT scene_key,scene_hash FROM breakdown_scene_approvals WHERE episode_id=?", (episode_id,))}
        return {scene["scene_key"] for scene in content["scenes"] if (scene["scene_key"], scene_hash(scene)) in approvals}

    def _write_revision(self, conn, episode: dict, content: dict, source: str, screenplay_id: str | None) -> int:
        current = self._revision(conn, episode["id"])
        gate = self._library(conn, episode["project_id"])
        if gate["blockers"]:
            raise DomainError("Chưa mở được breakdown: " + " ".join(gate["blockers"]), 409)
        content_hash = digest(content)
        if current and current["content_hash"] == content_hash:
            raise DomainError("Nội dung không thay đổi; không tạo phiên bản breakdown mới.", 409)
        version = (current["version"] if current else 0) + 1
        conn.execute("INSERT INTO breakdown_revisions(id,project_id,episode_id,version,screenplay_id,source,library_version,style_version,content_json,content_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (uid(), episode["project_id"], episode["id"], version, screenplay_id or (current["screenplay_id"] if current else None), source,
                      gate["library_version"], gate["style_version"], canonical(content), content_hash, now()))
        self.studio.audit(conn, episode["project_id"], "breakdown.revision", episode["id"], {"version": version, "source": source})
        return version

    # ---- 01 open episode ------------------------------------------------------------------------------------------

    def open_status(self, episode_id: str) -> dict:
        with self.db.connect() as conn:
            episode = self._episode(conn, episode_id)
            gate = self._library(conn, episode["project_id"])
            standards = conn.execute("SELECT fps_num,fps_den FROM series_standards WHERE project_id=?", (episode["project_id"],)).fetchone()
            scan = self.series._latest_scan(conn, episode["project_id"])
            library = self.series.library_state(conn, episode["project_id"])
            bible = self.style.bible_state(conn, episode["project_id"])
        unreal = self.studio.unreal_settings()
        checks = [
            {"key": "unreal", "label": "Kết nối Unreal và plugin", "ok": unreal["configured"],
             "detail": unreal["error"] or ("Đã cấu hình; đường dẫn và plugin hợp lệ." if unreal["configured"] else "Chưa cấu hình Unreal trong Cài đặt.")},
            {"key": "engine", "label": f"Phiên bản Unreal {UE_TARGET_VERSION}", "ok": bool(scan and scan["engine_version"] and scan["engine_version"].startswith(UE_TARGET_VERSION + ".")),
             "detail": f"Lần quét gần nhất báo Unreal {scan['engine_version']}." if scan else "Chưa có lần quét nào xác nhận phiên bản Unreal."},
            {"key": "fps", "label": "Chuẩn FPS của series", "ok": bool(standards),
             "detail": f"{standards['fps_num']}/{standards['fps_den']} fps theo S1; kiểm tra trong project Unreal thực hiện khi tạo sequence." if standards else "Chưa lưu chuẩn kỹ thuật S1."},
            {"key": "library", "label": "Thư viện mapping", "ok": bool(gate["library_version"]),
             "detail": f"Đang dùng thư viện v{gate['library_version']}" + (" · bản nháp có thay đổi chưa duyệt" if library["draft_changed"] else "") if gate["library_version"] else "Chưa duyệt CS1."},
            {"key": "style", "label": "Style bible", "ok": bool(gate["style_version"]),
             "detail": f"Đang dùng Style bible v{gate['style_version']}" + (" · bản nháp có thay đổi chưa duyệt" if bible["draft_changed"] else "") if gate["style_version"] else "Chưa duyệt CS2."},
        ]
        return {"episode": episode, "checks": checks, "ready": not gate["blockers"], "blockers": gate["blockers"],
                "library_version": gate["library_version"], "style_version": gate["style_version"],
                "last_scan_at": scan["created_at"] if scan else None, "scan_diff": self.series.scan_diff(episode["project_id"])}

    # ---- 03 breakdown jobs ----------------------------------------------------------------------------------------

    def _provider(self, settings: dict):
        if self.custom_provider is not None:
            return self.custom_provider
        if settings["provider_kind"] == "CLAUDE_CODE":
            return ClaudeCodeBreakdown(self.claude_code)
        return OpenAIBreakdown() if settings["provider_kind"] == "OPENAI" else FixtureBreakdown()

    def _request(self, conn, episode_id: str, screenplay_id: str) -> tuple[dict, object, dict]:
        episode = self._episode(conn, episode_id)
        script = require(conn.execute("SELECT * FROM screenplay_versions WHERE id=? AND episode_id=?", (screenplay_id, episode_id)).fetchone(), "screenplay")
        gate = self._library(conn, episode["project_id"])
        if gate["blockers"]:
            raise DomainError("Chưa phân tích được: " + " ".join(gate["blockers"]), 409)
        settings = self.ai_settings.get()
        provider = self._provider(settings)
        request = {"schema_version": "breakdown.request.1", "episode_id": episode_id, "screenplay_id": screenplay_id,
                   "screenplay_sha256": script["sha256"], "screenplay_text": script["text"], "language": script["language"],
                   "library_version": gate["library_version"], "style_version": gate["style_version"],
                   "library": [{"entity_key": e["entity_key"], "kind": e["kind"], "name": e["display_name"], "aliases": e["aliases"], "description": e["description"]}
                               for e in self._entities(conn, episode["project_id"])],
                   "style_phrases": gate["style_phrases"], "banned_words": gate["banned_words"], "provider_kind": provider.kind}
        if provider.kind == "OPENAI":
            request.update({"provider_model": settings["model"], "max_output_tokens": settings["max_output_tokens"], "settings_revision": settings["revision"]})
        elif provider.kind == "CLAUDE_CODE":
            chosen = self.claude_code.get()
            request.update({"provider_model": chosen["model"], "settings_revision": chosen["revision"]})
        request["input_hash"] = digest(request)
        return request, provider, episode

    def preview_job(self, episode_id: str, screenplay_id: str) -> dict:
        with self.db.connect() as conn:
            request, provider, episode = self._request(conn, episode_id, screenplay_id)
            estimate = provider.estimate_micro(request)
            gate = budget.check(conn, episode["project_id"], "USD", estimate) if provider.paid else {"allowed": True, "enforced": False, "reason": None}
        return {"provider_kind": provider.kind, "provider_model": request.get("provider_model"), "paid": provider.paid,
                "estimated_micro": estimate, "currency": "USD", "budget": gate,
                "note": getattr(provider, "note", None)}

    def enqueue(self, episode_id: str, screenplay_id: str, client_request_id: str) -> dict:
        try:
            uuid.UUID(client_request_id)
        except (ValueError, AttributeError) as exc:
            raise DomainError("client_request_id phải là UUID.") from exc
        with self.db.transaction() as conn:
            request, provider, episode = self._request(conn, episode_id, screenplay_id)
            operation_id = digest({"input_hash": request["input_hash"], "client_request_id": client_request_id})
            existing = conn.execute("SELECT * FROM breakdown_jobs WHERE operation_id=?", (operation_id,)).fetchone()
            if existing:
                return dict(existing)
            pending = conn.execute("SELECT * FROM breakdown_jobs WHERE episode_id=? AND state IN ('QUEUED','RUNNING','RECONCILING') ORDER BY created_at DESC LIMIT 1", (episode_id,)).fetchone()
            if pending:
                if pending["state"] == "RECONCILING":
                    raise DomainError("Lần phân tích trước chưa rõ kết quả; đóng khoản giữ trong Sổ chi phí trước khi gửi yêu cầu mới.", 409)
                return dict(pending)
            estimate = provider.estimate_micro(request)
            if provider.kind == "CLAUDE_CODE" and not self.claude_code.available():
                raise DomainError(CLAUDE_CODE_MISSING, 409)
            if provider.paid:
                if not self.secret_available():
                    raise DomainError("Chưa có khóa OpenAI trong kho bảo mật; vào Cài đặt để nhập khóa.", 409)
                budget.reserve(conn, episode["project_id"], operation_id, gate=0, category="PLANNING",
                               provider_kind=provider.kind, currency="USD", amount_micro=estimate)
            stamp = now()
            item = {"id": uid(), "project_id": episode["project_id"], "episode_id": episode_id, "screenplay_id": screenplay_id,
                    "provider_kind": provider.kind, "provider_model": request.get("provider_model"), "operation_id": operation_id,
                    "input_hash": request["input_hash"], "state": "QUEUED", "estimated_micro": estimate, "actual_micro": None,
                    "provider_request_id": None, "revision_version": None, "error": None, "created_at": stamp, "updated_at": stamp}
            conn.execute("INSERT INTO breakdown_jobs(id,project_id,episode_id,screenplay_id,provider_kind,provider_model,operation_id,input_hash,state,estimated_micro,actual_micro,provider_request_id,revision_version,error,created_at,updated_at) VALUES(:id,:project_id,:episode_id,:screenplay_id,:provider_kind,:provider_model,:operation_id,:input_hash,:state,:estimated_micro,:actual_micro,:provider_request_id,:revision_version,:error,:created_at,:updated_at)", item)
            self.studio.audit(conn, episode["project_id"], "breakdown.queued", item["id"], {"provider": provider.kind, "estimate_micro": estimate})
            return item

    def jobs(self, episode_id: str) -> list[dict]:
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM breakdown_jobs WHERE episode_id=? ORDER BY created_at DESC LIMIT 10", (episode_id,))]

    def _finish(self, job: dict, state: str, error: str | None, *, actual: int | None = None, request_id: str | None = None,
                version: int | None = None, model: str | None = None, release: bool = False) -> dict:
        with self.db.transaction() as conn:
            conn.execute("UPDATE breakdown_jobs SET state=?,error=?,actual_micro=COALESCE(?,actual_micro),provider_request_id=COALESCE(?,provider_request_id),revision_version=COALESCE(?,revision_version),provider_model=COALESCE(?,provider_model),updated_at=? WHERE id=?",
                         (state, error, actual, request_id, version, model, now(), job["id"]))
            if actual is not None:
                budget.settle(conn, job["operation_id"], actual)
            elif release:
                budget.release(conn, job["operation_id"])
            self.studio.audit(conn, job["project_id"], "breakdown." + state.lower(), job["id"], {"version": version})
            return dict(conn.execute("SELECT * FROM breakdown_jobs WHERE id=?", (job["id"],)).fetchone())

    def process_next(self) -> dict | None:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM breakdown_jobs WHERE state='QUEUED' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            job = dict(row)
            conn.execute("UPDATE breakdown_jobs SET state='RUNNING',updated_at=? WHERE id=?", (now(), job["id"]))
        sent = False
        try:
            with self.db.connect() as conn:
                request, provider, episode = self._request(conn, job["episode_id"], job["screenplay_id"])
            if request["input_hash"] != job["input_hash"]:
                raise PlannerNotSent("Kịch bản, thư viện hoặc cấu hình AI đã đổi sau khi xếp việc; yêu cầu chưa gửi.", 409)
            request["operation_id"] = job["operation_id"]
            sent = provider.paid
            result = provider.generate(request)
        except BreakdownTruncated as exc:
            return self._finish(job, "BLOCKED", str(exc), actual=min(exc.actual_micro, job["estimated_micro"]), request_id=exc.request_id)
        except Exception as exc:
            not_sent = isinstance(exc, PlannerNotSent) or not sent
            return self._finish(job, "BLOCKED" if not_sent else "RECONCILING", str(exc)[:500],
                                request_id=exc.request_id if isinstance(exc, PlannerRemoteError) else None, release=not_sent)
        if result.actual_micro < 0 or result.actual_micro > job["estimated_micro"]:
            return self._finish(job, "RECONCILING", "Chi phí thực vượt mức đã giữ; cần đối soát.", request_id=result.provider_request_id)
        try:
            with self.db.transaction() as conn:
                drafted = clean_draft(json.loads(result.raw_json), entity_index(self._entities(conn, episode["project_id"])))
                current = self._revision(conn, job["episode_id"])
                prior = json.loads(current["content_json"]) if current else None
                content = merge_draft(prior, drafted, self._approved_scenes(conn, job["episode_id"], prior) if prior else set())
                # A draft that changes nothing (every scene approved, or an identical rerun) keeps the current revision.
                version = current["version"] if current and digest(content) == current["content_hash"] else self._write_revision(conn, episode, content, provider.kind, job["screenplay_id"])
        except (DomainError, ValueError) as exc:
            # The request was answered and billed; only its content could not be used.
            return self._finish(job, "BLOCKED", "Kết quả phân tích không dùng được: " + str(exc)[:400],
                                actual=result.actual_micro, request_id=result.provider_request_id, model=result.resolved_model)
        return self._finish(job, "COMPLETED", None, actual=result.actual_micro, request_id=result.provider_request_id,
                            version=version, model=result.resolved_model)

    # ---- C1 review ------------------------------------------------------------------------------------------------

    def _c1(self, conn, episode: dict, revision) -> dict | None:
        row = conn.execute("SELECT * FROM workflow_checkpoints WHERE project_id=? AND checkpoint='C1' AND scope_id=? ORDER BY created_at DESC LIMIT 1", (episode["project_id"], episode["id"])).fetchone()
        if not row:
            return None
        return {"exact_version": row["exact_version"], "created_at": row["created_at"],
                "current": bool(revision) and row["dependency_hash"] == revision["content_hash"]}

    def current(self, episode_id: str) -> dict:
        with self.db.connect() as conn:
            episode = self._episode(conn, episode_id)
            gate = self._library(conn, episode["project_id"])
            revision = self._revision(conn, episode_id)
            entities = self._entities(conn, episode["project_id"])
            if not revision:
                return {"episode": episode, "revision": None, "scenes": [], "library": entities, "gate": gate, "c1": None,
                        "all_scenes_approved": False, "summary": {"scenes": 0, "shots": 0, "duration_seconds": 0}}
            content = json.loads(revision["content_json"])
            index = entity_index(entities)
            approved = self._approved_scenes(conn, episode_id, content)
            scenes = [dict(scene_review(scene, index, gate["banned_words"], gate["style_phrases"], episode["code"]),
                           approved=scene["scene_key"] in approved) for scene in content["scenes"]]
            c1 = self._c1(conn, episode, revision)
        return {"episode": episode, "gate": gate, "library": entities, "scenes": scenes, "c1": c1,
                "revision": {"version": revision["version"], "source": revision["source"], "created_at": revision["created_at"],
                             "content_hash": revision["content_hash"], "library_version": revision["library_version"], "style_version": revision["style_version"]},
                "all_scenes_approved": bool(scenes) and all(scene["approved"] for scene in scenes),
                "summary": {"scenes": len(scenes), "shots": sum(len(scene["shots"]) for scene in scenes),
                            "duration_seconds": round(sum(scene["duration_seconds"] for scene in scenes), 2)}}

    def _edit(self, episode_id: str, expected_hash: str | None, change) -> dict:
        with self.db.transaction() as conn:
            episode = self._episode(conn, episode_id)
            revision = self._revision(conn, episode_id)
            if (revision["content_hash"] if revision else None) != expected_hash:
                raise DomainError("Breakdown đã đổi ở phiên khác; tải lại trước khi sửa.", 409)
            content = json.loads(revision["content_json"]) if revision else {"schema_version": SCHEMA, "next_scene": 1, "scenes": []}
            index = entity_index(self._entities(conn, episode["project_id"]))
            self._write_revision(conn, episode, assign_keys(change(content, index)), "MANUAL_EDIT", None)
        return self.current(episode_id)

    def save_scene(self, episode_id: str, expected_content_hash: str, scene: dict) -> dict:
        return self._edit(episode_id, expected_content_hash, lambda content, index: apply_scene_edit(content, clean_scene(scene, index)))

    def add_scene(self, episode_id: str, expected_content_hash: str | None, after_scene_key: str | None, heading: str, environment_name: str) -> dict:
        def change(content, index):
            scene = clean_scene({"heading": heading, "summary": "", "environment": {"name": environment_name, "entity_key": None},
                                 "shots": [{"description": "Mô tả shot", "shot_size": "WIDE", "duration_seconds": 4, "face_visible": False}]}, index)
            scene["scene_key"] = None
            position = next((i + 1 for i, s in enumerate(content["scenes"]) if s["scene_key"] == after_scene_key), len(content["scenes"]))
            return dict(content, scenes=content["scenes"][:position] + [scene] + content["scenes"][position:])
        return self._edit(episode_id, expected_content_hash, change)

    def delete_scene(self, episode_id: str, expected_content_hash: str, scene_key: str) -> dict:
        def change(content, index):
            if not any(s["scene_key"] == scene_key for s in content["scenes"]):
                raise DomainError("Scene không còn trong breakdown hiện tại.", 409)
            return dict(content, scenes=[s for s in content["scenes"] if s["scene_key"] != scene_key])
        return self._edit(episode_id, expected_content_hash, change)

    def move_scene(self, episode_id: str, expected_content_hash: str, scene_key: str, direction: str) -> dict:
        def change(content, index):
            scenes = list(content["scenes"])
            position = next((i for i, s in enumerate(scenes) if s["scene_key"] == scene_key), None)
            target = None if position is None else position + (-1 if direction == "up" else 1)
            if direction not in ("up", "down") or target is None or not 0 <= target < len(scenes):
                raise DomainError("Không di chuyển được scene theo hướng này.", 409)
            scenes[position], scenes[target] = scenes[target], scenes[position]
            return dict(content, scenes=scenes)
        return self._edit(episode_id, expected_content_hash, change)

    def approve_scene(self, episode_id: str, scene_key: str, expected_scene_hash: str) -> dict:
        with self.db.transaction() as conn:
            episode = self._episode(conn, episode_id)
            revision = require(self._revision(conn, episode_id), "breakdown")
            gate = self._library(conn, episode["project_id"])
            scene = next((s for s in json.loads(revision["content_json"])["scenes"] if s["scene_key"] == scene_key), None)
            if scene is None or scene_hash(scene) != expected_scene_hash:
                raise DomainError("Scene đã đổi sau khi bạn xem; tải lại rồi duyệt đúng nội dung hiện tại.", 409)
            review = scene_review(scene, entity_index(self._entities(conn, episode["project_id"])), gate["banned_words"], gate["style_phrases"], episode["code"])
            if review["issues"]:
                raise DomainError("Chưa duyệt được scene: " + " ".join(review["issues"][:5]), 409)
            conn.execute("INSERT OR IGNORE INTO breakdown_scene_approvals(id,project_id,episode_id,scene_key,scene_hash,revision_version,actor,created_at) VALUES(?,?,?,?,?,?,'local_user',?)",
                         (uid(), episode["project_id"], episode_id, scene_key, expected_scene_hash, revision["version"], now()))
            self.studio.audit(conn, episode["project_id"], "breakdown.scene_approved", episode_id, {"scene": scene_key, "revision": revision["version"]})
        return self.current(episode_id)

    def approve_c1(self, episode_id: str, expected_content_hash: str) -> dict:
        with self.db.transaction() as conn:
            episode = self._episode(conn, episode_id)
            revision = require(self._revision(conn, episode_id), "breakdown")
            if revision["content_hash"] != expected_content_hash:
                raise DomainError("Breakdown đã đổi sau khi bạn xem; tải lại.", 409)
            content = json.loads(revision["content_json"])
            pending = [s["scene_key"] for s in content["scenes"] if s["scene_key"] not in self._approved_scenes(conn, episode_id, content)]
            if not content["scenes"] or pending:
                raise DomainError("Chưa chốt được breakdown: còn scene chưa duyệt (" + ", ".join(pending[:8]) + ").", 409)
            if conn.execute("SELECT 1 FROM workflow_checkpoints WHERE project_id=? AND checkpoint='C1' AND scope_id=? AND exact_version=?", (episode["project_id"], episode_id, revision["version"])).fetchone():
                raise DomainError("Phiên bản breakdown này đã được chốt.", 409)
            workflow.record_checkpoint(self.studio, conn, episode["project_id"], "C1", episode_id, revision["version"], revision["content_hash"],
                                       {"scenes": len(content["scenes"]), "shots": sum(len(s["shots"]) for s in content["scenes"]),
                                        "library_version": revision["library_version"], "style_version": revision["style_version"]})
        return self.current(episode_id)

    # ---- 04 compare with the library, C2 --------------------------------------------------------------------------

    def _compare(self, conn, episode: dict) -> dict:
        revision = self._revision(conn, episode["id"])
        library = self.series.library_state(conn, episode["project_id"])
        bible = self.style.bible_state(conn, episode["project_id"])
        c1 = self._c1(conn, episode, revision)
        if not revision:
            return {"revision": None, "library": library, "bible": bible, "c1": c1, "items": [], "missing": {"assets": [], "motions": [], "vfx": []}}
        content = json.loads(revision["content_json"])
        entities = {e["entity_key"]: e for e in library["state"]["entities"]}
        approved = {e["entity_key"]: e for e in library["snapshot"]["entities"]} if library["snapshot"] else {}
        used = usage(content, entity_index(list(entities.values())), episode["code"])
        items = []
        for kind in ("character", "environment"):
            for item in used[kind].values():
                entity = entities.get(item["entity_key"]) if item["entity_key"] else None
                mapping = entity["mapping"] if entity else None
                current_mapping = {k: mapping[k] for k in ("status", "asset_path", "body_mesh_path", "face_mesh_path", "note")} if mapping else None
                status = "NEW" if not entity else (mapping["status"] if mapping else "UNMAPPED")
                items.append({"kind": kind, "names": item["names"], "entity_key": item["entity_key"], "entity_id": entity["id"] if entity else None,
                              "display_name": entity["display_name"] if entity else item["names"][0], "status": status,
                              "asset_path": mapping["asset_path"] if mapping else None,
                              "in_approved_library": bool(entity) and entity["entity_key"] in approved and approved[entity["entity_key"]]["mapping"] == current_mapping,
                              "scenes": item["scenes"], "shot_count": len(item["shots"])})
        scan = library["state"]["scan"]
        motion, vfx = tag_usage(content, episode["code"])

        def unmatched(tags: dict, category: str, folders: list[str]) -> list[dict]:
            result = []
            if not scan or not folders:
                return result
            for tag, shots in sorted(tags.items()):
                found = False
                for folder in folders:
                    like = folder.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"
                    name = "%" + tag.replace(" ", "%").replace("\\", "\\\\").replace("_", "\\_") + "%"
                    if conn.execute("SELECT 1 FROM ue_scan_assets WHERE scan_id=? AND category=? AND (package_path=? OR package_path LIKE ? ESCAPE '\\') AND asset_name LIKE ? ESCAPE '\\' LIMIT 1",
                                    (scan["id"], category, folder, like, name)).fetchone():
                        found = True
                        break
                if not found:
                    result.append({"tag": tag, "shots": shots})
            return result

        draft = library["state"]["draft"]
        return {"revision": revision, "library": library, "bible": bible, "c1": c1, "items": items,
                "missing": {"assets": [i for i in items if i["status"] in ("NEW", "UNMAPPED", "MISSING")],
                            "motions": unmatched(motion, "animation", draft["animation_folders"]),
                            "vfx": unmatched(vfx, "niagara", draft["vfx_folders"])},
                "folders_selected": {"animation": bool(draft["animation_folders"]), "vfx": bool(draft["vfx_folders"])}}

    @staticmethod
    def _c2_hash(revision, library: dict, bible: dict) -> str:
        return digest({"content": revision["content_hash"], "library": library["draft_hash"], "style": bible["draft_hash"]})

    def compare(self, episode_id: str) -> dict:
        with self.db.connect() as conn:
            return self._comparison(conn, self._episode(conn, episode_id))

    def _comparison(self, conn, episode: dict, data: dict | None = None) -> dict:
        """Step 04 as the operator sees it, plus whether C2 still covers the current breakdown and library."""
        data = data or self._compare(conn, episode)
        revision, library, bible = data["revision"], data["library"], data["bible"]
        latest = conn.execute("SELECT * FROM workflow_checkpoints WHERE project_id=? AND checkpoint='C2' AND scope_id=? ORDER BY exact_version DESC LIMIT 1", (episode["project_id"], episode["id"])).fetchone()
        blockers = []
        if not revision:
            blockers.append("Chưa có breakdown của tập.")
        elif not data["c1"] or not data["c1"]["current"]:
            blockers.append("C1: breakdown hiện tại chưa được chốt.")
        new_names = [i for i in data["items"] if i["status"] == "NEW"]
        if new_names:
            blockers.append("Còn tên chưa có trong thư viện: " + ", ".join(i["display_name"] for i in new_names[:8]) + ". Thêm vào thư viện hoặc gán là tên gọi khác.")
        library_pending = not library["version"] or library["draft_changed"]
        if library_pending:
            blockers += library["blockers"]
        style_pending = library_pending or not bible["version"] or bible["draft_changed"]
        if style_pending and not library_pending:
            blockers += bible["blockers"]
        elif style_pending:
            # The Style bible is re-checked against the new library version inside the approval itself.
            blockers += [b for b in bible["blockers"] if not b.startswith("CS1")]
        c2_hash = self._c2_hash(revision, library, bible) if revision else None
        c2 = None
        if latest:
            decision = json.loads(latest["decision_json"])
            c2 = {"sequence": latest["exact_version"], "created_at": latest["created_at"], "decision": decision,
                  "current": bool(revision) and decision.get("revision_version") == revision["version"] and not library_pending
                             and bool(bible["version"]) and not bible["draft_changed"] and not new_names}
        return {"episode": episode, "revision_version": revision["version"] if revision else None, "items": data["items"],
                "missing": data["missing"], "folders_selected": data.get("folders_selected"),
                "library": {"version": library["version"], "draft_changed": library["draft_changed"]},
                "style": {"version": bible["version"], "draft_changed": bible["draft_changed"], "warnings": bible["warnings"]},
                "has_new_resources": library_pending or style_pending, "blockers": blockers, "allowed": not blockers,
                "expected_hash": c2_hash, "c2": c2,
                "motion_note": "Danh sách motion/VFX thiếu là ước lượng theo tên asset trong các thư mục đã chọn; bước gán animation sẽ kiểm tra chính xác."}

    def add_to_library(self, episode_id: str, kind: str, name: str) -> dict:
        with self.db.connect() as conn:
            episode = self._episode(conn, episode_id)
        self.series.add_entity(episode["project_id"], kind, name, [], "")
        return self.compare(episode_id)

    def link_alias(self, episode_id: str, name: str, entity_id: str) -> dict:
        """The name in the screenplay is another name for an entity the library already has."""
        with self.db.connect() as conn:
            entity = require(conn.execute("SELECT * FROM series_entities WHERE id=? AND active=1", (entity_id,)).fetchone(), "entity")
        self.series.update_entity(entity_id, entity["revision"], entity["display_name"], [*json.loads(entity["aliases_json"]), name], entity["description"])
        return self.compare(episode_id)

    def export_missing(self, episode_id: str) -> dict:
        comparison = self.compare(episode_id)
        if not comparison["revision_version"]:
            raise DomainError("Chưa có breakdown để xuất danh sách thiếu.", 409)
        episode = comparison["episode"]
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(["loai", "ten_hoac_nhan", "trang_thai", "so_shot", "chi_tiet"])
        for item in comparison["missing"]["assets"]:
            writer.writerow(["asset_" + item["kind"], item["display_name"], item["status"], item["shot_count"], " ".join(item["scenes"])])
        for kind in ("motions", "vfx"):
            for item in comparison["missing"][kind]:
                writer.writerow([kind[:-1] if kind == "motions" else kind, item["tag"], "CHUA_THAY_TRONG_THU_MUC", len(item["shots"]), " ".join(item["shots"])])
        root = Path(self.studio.project(episode["project_id"])["root"]).resolve()
        relative = f"manifests/missing-{episode['code']}-r{comparison['revision_version']}.csv"
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(buffer.getvalue(), encoding="utf-8-sig")
        return {"relative_path": relative, "rows": sum(len(v) for v in comparison["missing"].values())}

    def approve_c2(self, episode_id: str, expected_hash: str) -> dict:
        check = self.compare(episode_id)
        if check["blockers"]:
            raise DomainError("Chưa duyệt được C2: " + " · ".join(check["blockers"][:6]), 409)
        if check["expected_hash"] != expected_hash:
            raise DomainError("Breakdown, mapping hoặc Style bible đã đổi sau khi bạn xem; tải lại.", 409)
        with self.db.transaction() as conn:
            episode = self._episode(conn, episode_id)
            data = self._compare(conn, episode)
            revision, library, bible = data["revision"], data["library"], data["bible"]
            if self._c2_hash(revision, library, bible) != expected_hash:
                raise DomainError("Breakdown, mapping hoặc Style bible đã đổi sau khi bạn xem; tải lại.", 409)
            added = [i["entity_key"] for i in data["items"] if i["entity_key"] and not i["in_approved_library"]]
            library_version = library["version"]
            if not library["version"] or library["draft_changed"]:
                library_version = self.series.approve_library(conn, episode["project_id"])
            bible = self.style.bible_state(conn, episode["project_id"])
            style_version = bible["version"]
            if not bible["version"] or bible["draft_changed"]:
                style_version = self.style.approve_bible(conn, episode["project_id"])
            skipped = library_version == library["version"] and style_version == data["bible"]["version"]
            sequence = conn.execute("SELECT COALESCE(MAX(exact_version),0)+1 FROM workflow_checkpoints WHERE project_id=? AND checkpoint='C2' AND scope_id=?", (episode["project_id"], episode_id)).fetchone()[0]
            workflow.record_checkpoint(self.studio, conn, episode["project_id"], "C2", episode_id, sequence, expected_hash,
                                       {"skipped": skipped, "revision_version": revision["version"], "library_version": library_version,
                                        "style_version": style_version, "added_entities": [] if skipped else added,
                                        "missing_assets": len(data["missing"]["assets"])})
        return self.compare(episode_id)
