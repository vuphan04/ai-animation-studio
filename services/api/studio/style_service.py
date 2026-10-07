"""Style bible (workflow V4.5 S4) and its approval (CS2).

References are image files inside the project folder. A reference rendered by Unreal is only a picture for
review; approving it says nothing about how the asset behaves in a shot.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from . import gpu_queue, workflow
from .domain import DomainError, canonical, digest, file_sha256, UE_TARGET_VERSION
from .service import now, require, uid
from .style_domain import ROLES, banned_hits, build_prompt, clean_terms, probe_image
from .tool_settings import ToolSettings
from .voice_settings import PROVIDER as VOICE_PROVIDER


CAPTURE_PROVENANCE = "UE_SCENE_CAPTURE"
MANUAL_PROVENANCE = "OPERATOR_FILE"


class StyleService:
    def __init__(self, studio, series, *, capturer=None, flatten=None):
        self.studio = studio
        self.db = studio.db
        self.series = series
        self.tools = ToolSettings(studio)
        self.capturer = capturer or (lambda request: studio._unreal_adapter().capture_references(request))
        self.flatten = flatten or self._ffmpeg_flatten
        with self.db.transaction() as conn:
            conn.execute("UPDATE style_capture_jobs SET state='RECONCILING',error='Backend khởi động lại trong lúc chụp; đóng Unreal cũ rồi xác nhận.',updated_at=? WHERE state='RUNNING'", (now(),))

    def _ffmpeg_flatten(self, source: Path, target: Path) -> None:
        """Scene captures can carry an empty alpha channel; rewrite as opaque RGB so the frame is visible."""
        tools = self.tools.get()
        if not tools["configured"]:
            raise DomainError("Cấu hình FFmpeg trong Cài đặt trước khi chụp reference từ Unreal.", 409)
        completed = subprocess.run([tools["ffmpeg_path"], "-y", "-loglevel", "error", "-i", str(source), "-vf", "format=rgb24", str(target)],
                                   capture_output=True, text=True, timeout=120, check=False)
        if completed.returncode != 0 or not target.is_file():
            raise DomainError("FFmpeg không chuẩn hóa được ảnh chụp từ Unreal.", 409)

    # ---- draft ---------------------------------------------------------------------------------------------------

    @staticmethod
    def _draft(conn, project_id: str) -> dict:
        row = conn.execute("SELECT * FROM style_bible_drafts WHERE project_id=?", (project_id,)).fetchone()
        if not row:
            return {"revision": 0, "style_phrases": [], "banned_words": [], "turnaround_map_path": None}
        return {"revision": row["revision"], "style_phrases": json.loads(row["style_phrases_json"]),
                "banned_words": json.loads(row["banned_words_json"]), "turnaround_map_path": row["turnaround_map_path"]}

    def save_style(self, project_id: str, expected_revision: int, style_phrases: list, banned_words: list,
                   turnaround_map_path: str | None) -> dict:
        self.studio.project(project_id)
        phrases = clean_terms(style_phrases, limit=30, max_length=120, label="Cụm từ style")
        banned = clean_terms(banned_words, limit=100, max_length=60, label="Từ cấm")
        with self.db.transaction() as conn:
            if self._draft(conn, project_id)["revision"] != expected_revision:
                raise DomainError("Style bible đã đổi ở phiên khác; tải lại trước khi lưu.", 409)
            if turnaround_map_path:
                scan = self.series._latest_scan(conn, project_id)
                if not scan:
                    raise DomainError("Chưa có lần quét asset Unreal hoàn tất.", 409)
                self.series._require_asset(conn, scan["id"], turnaround_map_path, ("map",), "Level chụp turnaround")
            conn.execute("INSERT OR REPLACE INTO style_bible_drafts(project_id,revision,style_phrases_json,banned_words_json,turnaround_map_path,updated_at) VALUES(?,?,?,?,?,?)",
                         (project_id, expected_revision + 1, canonical(phrases), canonical(banned), turnaround_map_path or None, now()))
            self.studio.audit(conn, project_id, "style.draft_saved", project_id, {"revision": expected_revision + 1})
        return self.state(project_id)

    def save_profile(self, entity_id: str, expected_revision: int, prompt_description: str, voice_mode: str,
                     voice_id: str | None) -> dict:
        if not isinstance(prompt_description, str) or len(prompt_description) > 600 or voice_mode not in ("UNSET", "ELEVENLABS", "NO_DIALOGUE"):
            raise DomainError("Mô tả hình ảnh tối đa 600 ký tự; chọn giọng ElevenLabs hoặc đánh dấu không có thoại.")
        with self.db.transaction() as conn:
            entity = require(conn.execute("SELECT * FROM series_entities WHERE id=? AND active=1", (entity_id,)).fetchone(), "entity")
            row = conn.execute("SELECT revision FROM style_entity_profiles WHERE entity_id=?", (entity_id,)).fetchone()
            if (row["revision"] if row else 0) != expected_revision:
                raise DomainError("Hồ sơ style của mục này đã đổi ở phiên khác; tải lại trước khi lưu.", 409)
            voice_name = None
            if entity["kind"] != "character":
                voice_mode, voice_id = "UNSET", None
            elif voice_mode == "ELEVENLABS":
                voice = conn.execute("SELECT name FROM voice_catalog WHERE provider=? AND voice_id=?", (VOICE_PROVIDER, voice_id)).fetchone()
                if not voice:
                    raise DomainError("Chọn giọng trong danh sách đã tải từ ElevenLabs; không nhập ID tự do.", 409)
                voice_name = voice["name"]
            else:
                voice_id = None
            conn.execute("INSERT OR REPLACE INTO style_entity_profiles(entity_id,project_id,prompt_description,voice_mode,voice_id,voice_name,revision,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                         (entity_id, entity["project_id"], prompt_description.strip(), voice_mode, voice_id, voice_name, expected_revision + 1, now()))
            self.studio.audit(conn, entity["project_id"], "style.profile_saved", entity_id, {"key": entity["entity_key"], "voice_mode": voice_mode})
        return self.state(entity["project_id"])

    # ---- references ----------------------------------------------------------------------------------------------

    def _register(self, conn, entity, role: str, source: Path, provenance: str, source_asset_path: str | None) -> dict:
        """Copy an image into the project and record it as the next version of this role."""
        if role not in ROLES[entity["kind"]]:
            raise DomainError("Vai trò ảnh tham chiếu không hợp lệ cho loại mục này.")
        extension, width, height = probe_image(source)
        root = Path(self.studio.project(entity["project_id"])["root"]).resolve()
        version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM style_references WHERE entity_id=? AND role=?", (entity["id"], role)).fetchone()[0]
        relative = f"media/style/{entity['entity_key']}/{role}-v{version}.{extension}"
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise DomainError("Đã có file reference cùng tên trong dự án; kiểm tra thư mục media/style trước khi nhập lại.", 409)
        shutil.copyfile(source, target)
        item = {"id": uid(), "project_id": entity["project_id"], "entity_id": entity["id"], "role": role, "version": version,
                "relative_path": relative, "sha256": file_sha256(target), "bytes": target.stat().st_size, "width": width, "height": height,
                "provenance": provenance, "source_asset_path": source_asset_path, "created_at": now()}
        conn.execute("UPDATE style_references SET active=0 WHERE entity_id=? AND role=?", (entity["id"], role))
        conn.execute("INSERT INTO style_references(id,project_id,entity_id,role,version,relative_path,sha256,bytes,width,height,provenance,source_asset_path,review,active,created_at) VALUES(:id,:project_id,:entity_id,:role,:version,:relative_path,:sha256,:bytes,:width,:height,:provenance,:source_asset_path,'NEEDS_REVIEW',1,:created_at)", item)
        return item

    def import_reference(self, entity_id: str, role: str, source_path: str) -> dict:
        source = Path(source_path)
        if not source.is_file():
            raise DomainError("Không tìm thấy file ảnh đã chọn.")
        with self.db.transaction() as conn:
            entity = require(conn.execute("SELECT * FROM series_entities WHERE id=? AND active=1", (entity_id,)).fetchone(), "entity")
            item = self._register(conn, entity, role, source, MANUAL_PROVENANCE, None)
            self.studio.audit(conn, entity["project_id"], "style.reference_imported", item["id"], {"key": entity["entity_key"], "role": role})
        return self.state(entity["project_id"])

    def _reference(self, conn, reference_id: str) -> dict:
        return dict(require(conn.execute("SELECT * FROM style_references WHERE id=?", (reference_id,)).fetchone(), "reference"))

    def reference_file(self, reference_id: str) -> Path:
        with self.db.connect() as conn:
            reference = self._reference(conn, reference_id)
        path = Path(self.studio.project(reference["project_id"])["root"]).resolve() / reference["relative_path"]
        if not path.is_file() or file_sha256(path) != reference["sha256"]:
            raise DomainError("File reference thiếu hoặc đã bị đổi ngoài app.", 409)
        return path

    def review_reference(self, reference_id: str, decision: str) -> dict:
        if decision not in ("APPROVED", "REJECTED"):
            raise DomainError("Quyết định duyệt reference không hợp lệ.")
        self.reference_file(reference_id)  # The decision must be about the exact file that was registered.
        with self.db.transaction() as conn:
            reference = self._reference(conn, reference_id)
            if not reference["active"]:
                raise DomainError("Reference này đã được thay bằng phiên bản mới.", 409)
            conn.execute("UPDATE style_references SET review=? WHERE id=?", (decision, reference_id))
            self.studio.audit(conn, reference["project_id"], "style.reference_reviewed", reference_id, {"decision": decision})
        return self.state(reference["project_id"])

    def remove_reference(self, reference_id: str) -> dict:
        with self.db.transaction() as conn:
            reference = self._reference(conn, reference_id)
            conn.execute("UPDATE style_references SET active=0 WHERE id=?", (reference_id,))
            self.studio.audit(conn, reference["project_id"], "style.reference_removed", reference_id, {})
        return self.state(reference["project_id"])

    # ---- UE capture ----------------------------------------------------------------------------------------------

    def enqueue_capture(self, project_id: str, entity_ids: list[str] | None = None) -> dict:
        self.studio.project(project_id)
        self.studio._unreal_adapter()  # Fail before enqueue if Unreal is not configured.
        if self.flatten == self._ffmpeg_flatten and not self.tools.get()["configured"]:
            raise DomainError("Cấu hình FFmpeg trong Cài đặt trước khi chụp reference từ Unreal.", 409)
        with self.db.transaction() as conn:
            active = conn.execute("SELECT * FROM style_capture_jobs WHERE state IN ('QUEUED','RUNNING','RECONCILING') LIMIT 1").fetchone()
            if active:
                if active["state"] == "RECONCILING":
                    raise DomainError("Lần chụp trước bị gián đoạn; đóng Unreal cũ và xác nhận trước khi chụp lại.", 409)
                return self._job(active)
            state = self.series._state(conn, project_id)
            draft = self._draft(conn, project_id)
            standards = conn.execute("SELECT width,height FROM series_standards WHERE project_id=?", (project_id,)).fetchone()
            if not standards or not state["scan"]:
                raise DomainError("Lưu chuẩn kỹ thuật (S1) và quét asset (S3) trước khi chụp reference.", 409)
            items, skipped = [], []
            for entity in state["entities"]:
                if entity_ids is not None and entity["id"] not in entity_ids:
                    continue
                mapping = entity["mapping"]
                if not mapping or mapping["status"] != "MAPPED":
                    skipped.append({"entity_key": entity["entity_key"], "reason": "Chưa có asset; nhập ảnh tham chiếu bằng tay."})
                elif entity["kind"] == "character" and not draft["turnaround_map_path"]:
                    skipped.append({"entity_key": entity["entity_key"], "reason": "Chưa chọn level chụp turnaround."})
                else:
                    items.append({"entity_key": entity["entity_key"], "kind": entity["kind"], "asset_path": mapping["asset_path"],
                                  "map_path": draft["turnaround_map_path"] if entity["kind"] == "character" else mapping["asset_path"]})
            if not items:
                raise DomainError("Không có mục nào chụp được: " + "; ".join(f"{s['entity_key']}: {s['reason']}" for s in skipped[:5]), 409)
            operation_id = digest({"capture": uid(), "project": project_id})
            output = self.db.data_root / "unreal-captures" / operation_id / "frames"
            request = {"schema_version": "series.capture-request.1", "operation_id": operation_id, "items": items,
                       "output_dir": str(output), "width": min(standards["width"], 1920), "height": min(standards["height"], 1080)}
            item = {"id": uid(), "project_id": project_id, "operation_id": operation_id, "state": "QUEUED",
                    "request_json": canonical(request), "result_json": canonical({"skipped": skipped}), "error": None,
                    "created_at": now(), "updated_at": now()}
            conn.execute("INSERT INTO style_capture_jobs(id,project_id,operation_id,state,request_json,result_json,error,created_at,updated_at) VALUES(:id,:project_id,:operation_id,:state,:request_json,:result_json,:error,:created_at,:updated_at)", item)
            self.studio.audit(conn, project_id, "style.capture_queued", item["id"], {"items": len(items)})
            return self._job(item)

    @staticmethod
    def _job(row) -> dict:
        item = dict(row)
        request = json.loads(item.pop("request_json"))
        item["items"] = len(request["items"])
        item["result"] = json.loads(item.pop("result_json")) if item.get("result_json") else None
        return item

    def process_next_capture(self) -> dict | None:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM style_capture_jobs WHERE state='QUEUED' ORDER BY created_at LIMIT 1").fetchone()
            if not row or gpu_queue.holder(conn):
                return None
            job = dict(row)
            gpu_queue.try_acquire(conn, job["operation_id"], "UNREAL")
            conn.execute("UPDATE style_capture_jobs SET state='RUNNING',updated_at=? WHERE id=?", (now(), job["id"]))
        request = json.loads(job["request_json"])
        output = Path(request["output_dir"])
        try:
            output.mkdir(parents=True, exist_ok=True)
            result = self.capturer(request)
            if not isinstance(result, dict) or result.get("schema_version") != "series.capture-result.1" or result.get("adapter_kind") != "UNREAL_EDITOR_PYTHON" or result.get("operation_id") != job["operation_id"] or result.get("status") != "CAPTURED":
                raise DomainError("Kết quả chụp từ Unreal sai schema, nguồn hoặc operation.", 409)
            if not isinstance(result.get("engine_version"), str) or not result["engine_version"].startswith(UE_TARGET_VERSION + "."):
                raise DomainError(f"App viết cho Unreal {UE_TARGET_VERSION}; lần chụp báo phiên bản {result.get('engine_version')!r}.", 409)
            wanted = {item["entity_key"]: item for item in request["items"]}
            skipped = json.loads(job["result_json"])["skipped"] + [s for s in result.get("skipped", []) if isinstance(s, dict) and s.get("entity_key") in wanted]
            registered = 0
            with self.db.transaction() as conn:
                for image in result.get("images", []):
                    source = (output / str(image.get("file"))).resolve()
                    item = wanted.get(image.get("entity_key"))
                    if item is None or not source.is_relative_to(output.resolve()) or not source.is_file():
                        raise DomainError("Unreal báo một ảnh không thuộc yêu cầu hoặc không có trên đĩa.", 409)
                    flat = source.with_name(source.stem + "-rgb.png")
                    self.flatten(source, flat)
                    entity = require(conn.execute("SELECT * FROM series_entities WHERE project_id=? AND entity_key=? AND active=1", (job["project_id"], item["entity_key"])).fetchone(), "entity")
                    self._register(conn, entity, image.get("role"), flat, CAPTURE_PROVENANCE, item["asset_path"])
                    registered += 1
                conn.execute("UPDATE style_capture_jobs SET state='COMPLETED',result_json=?,error=NULL,updated_at=? WHERE id=?",
                             (canonical({"registered": registered, "skipped": skipped, "engine_version": result["engine_version"]}), now(), job["id"]))
                gpu_queue.release(conn, job["operation_id"])
                self.studio.audit(conn, job["project_id"], "style.capture_completed", job["id"], {"registered": registered, "skipped": len(skipped)})
        except Exception as exc:
            # Nothing was saved in Unreal and the editor process has ended, so the GPU can be handed on.
            with self.db.transaction() as conn:
                conn.execute("UPDATE style_capture_jobs SET state='BLOCKED',error=?,updated_at=? WHERE id=?",
                             (str(exc)[:500] if isinstance(exc, DomainError) else f"{type(exc).__name__}: không chụp được reference từ Unreal.", now(), job["id"]))
                gpu_queue.release(conn, job["operation_id"])
        with self.db.connect() as conn:
            return self._job(conn.execute("SELECT * FROM style_capture_jobs WHERE id=?", (job["id"],)).fetchone())

    def release_interrupted_capture(self, job_id: str, worker_stopped: bool) -> dict:
        with self.db.transaction() as conn:
            job = require(conn.execute("SELECT * FROM style_capture_jobs WHERE id=?", (job_id,)).fetchone(), "capture job")
            if job["state"] != "RECONCILING":
                raise DomainError("Lần chụp này không ở trạng thái cần xác nhận.", 409)
            if not worker_stopped:
                raise DomainError("Đóng cửa sổ Unreal của lần chụp cũ rồi xác nhận trong giao diện.", 409)
            conn.execute("UPDATE style_capture_jobs SET state='BLOCKED',error='Bị gián đoạn; chụp lại khi cần.',updated_at=? WHERE id=?", (now(), job_id))
            gpu_queue.release(conn, job["operation_id"])
        return self.state(job["project_id"])

    # ---- state and CS2 -------------------------------------------------------------------------------------------

    def _collect(self, conn, project_id: str) -> dict:
        library = self.series.library_state(conn, project_id)
        draft = self._draft(conn, project_id)
        root = Path(self.studio.project(project_id)["root"]).resolve()
        standards = conn.execute("SELECT lut_name,color_space FROM series_standards WHERE project_id=?", (project_id,)).fetchone()
        voices = {r[0] for r in conn.execute("SELECT voice_id FROM voice_catalog WHERE provider=?", (VOICE_PROVIDER,))}
        entities = []
        for entity in library["state"]["entities"]:
            mapping = entity["mapping"]
            profile = conn.execute("SELECT * FROM style_entity_profiles WHERE entity_id=?", (entity["id"],)).fetchone()
            profile = dict(profile) if profile else {"prompt_description": "", "voice_mode": "UNSET", "voice_id": None, "voice_name": None, "revision": 0}
            references = []
            for row in conn.execute("SELECT * FROM style_references WHERE entity_id=? AND active=1 ORDER BY role", (entity["id"],)):
                reference = dict(row)
                file = root / reference["relative_path"]
                reference["file_ok"] = file.is_file() and file.stat().st_size == reference["bytes"]
                # A frame rendered from an asset stops describing the entity once another asset is mapped.
                reference["stale"] = reference["provenance"] == CAPTURE_PROVENANCE and (not mapping or mapping["asset_path"] != reference["source_asset_path"])
                references.append(reference)
            sample = build_prompt(profile["prompt_description"] or entity["description"], draft["style_phrases"])
            entities.append({"id": entity["id"], "entity_key": entity["entity_key"], "kind": entity["kind"], "display_name": entity["display_name"],
                             "description": entity["description"], "mapping_status": mapping["status"] if mapping else None,
                             "asset_path": mapping["asset_path"] if mapping else None,
                             "profile": {k: profile[k] for k in ("prompt_description", "voice_mode", "voice_id", "voice_name", "revision")},
                             "voice_in_catalog": profile["voice_mode"] != "ELEVENLABS" or profile["voice_id"] in voices,
                             "references": references, "sample_prompt": sample, "banned_hits": banned_hits(sample, draft["banned_words"])})
        return {"library": library, "draft": draft, "entities": entities,
                "lut_name": standards["lut_name"] if standards else "", "color_space": standards["color_space"] if standards else ""}

    @staticmethod
    def _blockers(data: dict) -> tuple[list[str], list[str]]:
        blockers, warnings = [], []
        library, draft = data["library"], data["draft"]
        if not library["version"]:
            blockers.append("CS1: chưa có version thư viện được duyệt.")
        elif library["draft_changed"]:
            blockers.append("CS1: danh sách hoặc mapping đã đổi sau lần duyệt; duyệt lại CS1 trước.")
        if not draft["style_phrases"]:
            blockers.append("Chưa có cụm từ style cố định.")
        hits = banned_hits(", ".join(draft["style_phrases"]), draft["banned_words"])
        if hits:
            blockers.append("Cụm từ style chứa từ cấm: " + ", ".join(hits) + ".")
        for entity in data["entities"]:
            name = entity["display_name"]
            usable = [r for r in entity["references"] if not r["stale"] and r["file_ok"]]
            if any(r["stale"] for r in entity["references"]):
                blockers.append(f"{name}: có reference chụp từ asset cũ; chụp lại hoặc gỡ bỏ.")
            if any(not r["file_ok"] for r in entity["references"]):
                blockers.append(f"{name}: file reference thiếu hoặc bị đổi ngoài app.")
            if any(r["review"] == "NEEDS_REVIEW" for r in usable):
                blockers.append(f"{name}: còn reference chưa duyệt.")
            approved = [r for r in usable if r["review"] == "APPROVED"]
            if entity["mapping_status"] == "MAPPED" and not approved:
                blockers.append(f"{name}: cần ít nhất một reference đã duyệt.")
            if entity["mapping_status"] != "MAPPED" and not approved:
                warnings.append(f"{name}: đang Thiếu asset và chưa có reference; shot AI sẽ chỉ dựa vào mô tả chữ.")
            if entity["mapping_status"] != "MAPPED" and not entity["profile"]["prompt_description"]:
                blockers.append(f"{name}: đang Thiếu asset nên cần mô tả hình ảnh cho prompt.")
            if entity["banned_hits"]:
                blockers.append(f"{name}: prompt mẫu chứa từ cấm: " + ", ".join(entity["banned_hits"]) + ".")
            if entity["kind"] == "character":
                if entity["profile"]["voice_mode"] == "UNSET":
                    blockers.append(f"{name}: chưa gán giọng ElevenLabs hoặc đánh dấu không có thoại.")
                elif not entity["voice_in_catalog"]:
                    blockers.append(f"{name}: giọng đã gán không còn trong danh sách ElevenLabs; chọn lại.")
        return blockers, warnings

    @staticmethod
    def _snapshot(data: dict) -> dict:
        return {"schema_version": "series.style.1", "library_version": data["library"]["version"],
                "library_hash": data["library"]["snapshot_hash"], "lut_name": data["lut_name"], "color_space": data["color_space"],
                "style_phrases": data["draft"]["style_phrases"], "banned_words": data["draft"]["banned_words"],
                # Test shots need a live video provider; recorded so a later approval can require them.
                "ai_test_shots": "SKIPPED_NO_VIDEO_PROVIDER",
                "entities": [{"entity_key": e["entity_key"], "kind": e["kind"], "prompt_description": e["profile"]["prompt_description"],
                              "sample_prompt": e["sample_prompt"],
                              "voice": {"mode": e["profile"]["voice_mode"], "voice_id": e["profile"]["voice_id"], "voice_name": e["profile"]["voice_name"]},
                              "references": [{"role": r["role"], "version": r["version"], "relative_path": r["relative_path"], "sha256": r["sha256"], "provenance": r["provenance"]}
                                             for r in e["references"] if r["review"] == "APPROVED" and not r["stale"]]}
                             for e in data["entities"]]}

    def bible_state(self, conn, project_id: str) -> dict:
        data = self._collect(conn, project_id)
        blockers, warnings = self._blockers(data)
        approved = conn.execute("SELECT * FROM style_bible_versions WHERE project_id=? ORDER BY version DESC LIMIT 1", (project_id,)).fetchone()
        draft_hash = digest(self._snapshot(data))
        return {"data": data, "blockers": blockers, "warnings": warnings, "draft_hash": draft_hash,
                "version": approved["version"] if approved else 0,
                "snapshot": json.loads(approved["snapshot_json"]) if approved else None,
                "created_at": approved["created_at"] if approved else None,
                "draft_changed": bool(approved) and approved["snapshot_hash"] != draft_hash}

    def state(self, project_id: str) -> dict:
        self.studio.project(project_id)
        with self.db.connect() as conn:
            bible = self.bible_state(conn, project_id)
            jobs = [self._job(r) for r in conn.execute("SELECT * FROM style_capture_jobs WHERE project_id=? ORDER BY created_at DESC LIMIT 5", (project_id,))]
            scan = self.series._latest_scan(conn, project_id)
            maps = [dict(r) for r in conn.execute("SELECT object_path,asset_name FROM ue_scan_assets WHERE scan_id=? AND category='map' ORDER BY asset_name", (scan["id"],))] if scan else []
        data = bible["data"]
        return {"draft": data["draft"], "entities": data["entities"], "lut_name": data["lut_name"], "maps": maps, "roles": ROLES,
                "library": {"version": data["library"]["version"], "draft_changed": data["library"]["draft_changed"]},
                "blockers": bible["blockers"], "warnings": bible["warnings"], "allowed": not bible["blockers"], "draft_hash": bible["draft_hash"],
                "bible": {"version": bible["version"], "created_at": bible["created_at"], "draft_changed": bible["draft_changed"]} if bible["version"] else None,
                "capture_jobs": jobs, "ai_test_shots": "NOT_AVAILABLE"}

    def approve_bible(self, conn, project_id: str, expected_draft_hash: str | None = None) -> int:
        """Register the current Style bible as the next version (CS2, and C2 when an episode adds resources)."""
        bible = self.bible_state(conn, project_id)
        if bible["blockers"]:
            raise DomainError("Chưa duyệt được Style bible (CS2): " + " · ".join(bible["blockers"][:6]), 409)
        if expected_draft_hash is not None and bible["draft_hash"] != expected_draft_hash:
            raise DomainError("Style bible đã đổi sau khi bạn xem; tải lại rồi duyệt đúng bản hiện tại.", 409)
        if bible["version"] and not bible["draft_changed"]:
            raise DomainError("Bản này đã được duyệt; không có thay đổi để tăng version Style bible.", 409)
        root = Path(self.studio.project(project_id)["root"]).resolve()
        snapshot = self._snapshot(bible["data"])
        for entity in snapshot["entities"]:
            for reference in entity["references"]:
                if file_sha256(root / reference["relative_path"]) != reference["sha256"]:
                    raise DomainError(f"File reference của {entity['entity_key']} đã bị đổi ngoài app.", 409)
        version = bible["version"] + 1
        conn.execute("INSERT INTO style_bible_versions(id,project_id,version,library_version,snapshot_json,snapshot_hash,created_at) VALUES(?,?,?,?,?,?,?)",
                     (uid(), project_id, version, snapshot["library_version"], canonical(snapshot), bible["draft_hash"], now()))
        workflow.record_checkpoint(self.studio, conn, project_id, "CS2", project_id, version, bible["draft_hash"],
                                   {"library_version": snapshot["library_version"], "entities": len(snapshot["entities"]),
                                    "ai_test_shots": snapshot["ai_test_shots"]})
        return version

    def approve_cs2(self, project_id: str, expected_draft_hash: str) -> dict:
        self.studio.project(project_id)
        with self.db.transaction() as conn:
            self.approve_bible(conn, project_id, expected_draft_hash)
        return self.state(project_id)
