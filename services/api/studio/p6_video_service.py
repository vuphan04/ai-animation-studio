"""P6 video orchestration. Provider writes are separate from file and human review.

The default provider is deliberately disabled. An injected provider is used only
by contract tests until the account, price and upload/reconcile profile is proven.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .domain import DomainError, canonical, digest, safe_relative
from .p4_domain import file_sha256
from .service import Studio, now, require, uid
from .video_adapter import DisabledSeedanceProvider, VideoProvider


class VideoService:
    def __init__(self, studio: Studio, provider: VideoProvider | None = None, cache_render=None):
        self.studio = studio
        self._provider = provider
        self.cache_render = cache_render
        with studio.db.transaction() as conn:
            conn.execute("UPDATE video_jobs SET state='RECONCILING',error='App khởi động lại; đối soát provider ID trước khi tiếp tục.' WHERE state='RUNNING'")

    @property
    def provider(self):
        if self._provider is not None:
            return self._provider
        from .media_settings import MediaSettings
        return MediaSettings(self.studio).disabled_provider()

    def capability(self) -> dict:
        return dict(asdict(self.provider.capabilities), presets={"DRAFT": "480p", "FINAL": "720p"},
                    live_verified=False, note="Capability từ tài liệu; account, giá và chất lượng chưa được kiểm chứng.")

    def _shot(self, conn, shot_id: str):
        return require(conn.execute("SELECT * FROM shots WHERE id=?", (shot_id,)).fetchone(), "shot")

    def _approval(self, conn, shot) -> str | None:
        row = conn.execute("SELECT decision_json FROM approvals WHERE project_id=? AND scope='plan' AND scope_id=? AND exact_version=?",
                           (shot["project_id"], shot["id"], shot["current_revision"])).fetchone()
        if not row:
            return None
        decision = conn.execute("SELECT route FROM media_route_decisions WHERE shot_id=? AND shot_revision=? ORDER BY version DESC LIMIT 1",
                                (shot["id"], shot["current_revision"])).fetchone()
        return decision[0] if decision else json.loads(row[0])["approved_route"]

    def approve_route(self, shot_id: str, expected_shot_revision: int, route: str,
                      fallback_take_id: str | None = None) -> dict:
        if route not in ("UNREAL", "SEEDANCE", "HYBRID"):
            raise DomainError("Route C5 không hợp lệ.")
        with self.studio.db.transaction() as conn:
            shot = self._shot(conn, shot_id)
            if shot["current_revision"] != expected_shot_revision or not self._approval(conn, shot):
                raise DomainError("C5 cần kế hoạch C1 được duyệt đúng revision.", 409)
            if fallback_take_id:
                take = require(conn.execute("SELECT * FROM video_takes WHERE id=? AND shot_id=?", (fallback_take_id, shot_id)).fetchone(), "UE fallback take")
                if route != "UNREAL" or take["route"] != "UNREAL" or take["stage"] != "FINAL" or take["review"] != "APPROVED" or take["validity"] != "CURRENT" or take["shot_revision"] != expected_shot_revision:
                    raise DomainError("UE fallback phải là final take đã review đúng phiên bản.", 409)
                self._checked_file(shot["project_id"], take["relative_path"], take["sha256"])
            version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM media_route_decisions WHERE shot_id=? AND shot_revision=?", (shot_id, expected_shot_revision)).fetchone()[0]
            item = {"id": uid(), "project_id": shot["project_id"], "shot_id": shot_id,
                    "shot_revision": expected_shot_revision, "version": version, "route": route,
                    "fallback_take_id": fallback_take_id, "actor": "local_user", "created_at": now()}
            conn.execute("INSERT INTO media_route_decisions VALUES(:id,:project_id,:shot_id,:shot_revision,:version,:route,:fallback_take_id,:actor,:created_at)", item)
            conn.execute("UPDATE video_reference_packages SET validity='STALE' WHERE shot_id=? AND route!=?", (shot_id, route))
            self._stale_picture(conn, shot_id)
            self.studio.audit(conn, shot["project_id"], "media.route_approved", item["id"], {"version": version, "route": route, "fallback_take_id": fallback_take_id})
            return item

    def route_decisions(self, shot_id: str) -> list[dict]:
        self.studio.shot(shot_id)
        with self.studio.db.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM media_route_decisions WHERE shot_id=? ORDER BY created_at DESC", (shot_id,))]

    def import_ue_video(self, shot_id: str, expected_shot_revision: int, relative_path: str,
                        sha256: str, duration_frames: int, fps: dict) -> dict:
        """Human file intake. This cannot manufacture a verified MRQ receipt."""
        shot = self.studio.shot(shot_id)
        relative, path = self._checked_file(shot["project_id"], relative_path, sha256)
        if path.suffix.lower() not in (".mp4", ".mov") or type(duration_frames) is not int or duration_frames <= 0 or fps != shot["manifest"]["fps"] or duration_frames != shot["manifest"]["timeline"]["out_exclusive"] - shot["manifest"]["timeline"]["in"]:
            raise DomainError("UE video cần MP4/MOV và FPS/duration khai báo đúng manifest.")
        with self.studio.db.transaction() as conn:
            current = self._shot(conn, shot_id)
            if current["current_revision"] != expected_shot_revision:
                raise DomainError("Shot revision đổi trước khi nhập UE video.", 409)
            previous = conn.execute("SELECT * FROM video_takes WHERE shot_id=? AND shot_revision=? AND route='UNREAL' AND relative_path=? AND sha256=?", (shot_id, expected_shot_revision, relative, sha256.lower())).fetchone()
            if previous:
                return dict(previous)
            version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM video_takes WHERE shot_id=? AND stage='FINAL'", (shot_id,)).fetchone()[0]
            take_id = uid()
            conn.execute("INSERT INTO video_takes(id,project_id,shot_id,shot_revision,stage,route,version,relative_path,sha256,duration_frames,fps_num,fps_den,provenance,created_at) VALUES(?,?,?,?,'FINAL','UNREAL',?,?,?,?,?,?,'MANUAL_UE_FILE_UNVERIFIED',?)",
                         (take_id, shot["project_id"], shot_id, expected_shot_revision, version, relative, sha256.lower(), duration_frames, fps["num"], fps["den"], now()))
            self.studio.audit(conn, shot["project_id"], "media.ue_file_imported", take_id, {"version": version, "sha256": sha256.lower(), "metadata_verification": "USER_DECLARED_UNVERIFIED"})
            return dict(conn.execute("SELECT * FROM video_takes WHERE id=?", (take_id,)).fetchone())

    def _path(self, project_id: str, relative_path: str) -> tuple[str, Path]:
        root = Path(self.studio.project(project_id)["root"]).resolve()
        relative = safe_relative(root, relative_path)
        return relative, (root / relative).resolve()

    def _checked_file(self, project_id: str, relative_path: str, sha256: str) -> tuple[str, Path]:
        if not isinstance(sha256, str) or len(sha256) != 64 or any(c not in "0123456789abcdefABCDEF" for c in sha256):
            raise DomainError("SHA-256 cần đúng 64 ký tự hex.")
        relative, path = self._path(project_id, relative_path)
        if not path.is_file() or file_sha256(path) != sha256.lower():
            raise DomainError("Media thiếu hoặc checksum thay đổi.", 409)
        return relative, path

    def create_grant(self, project_id: str, currency: str, max_micro: int, expires_at: str) -> dict:
        self.studio.project(project_id)
        if currency != self.provider.capabilities.currency or type(max_micro) is not int or max_micro <= 0:
            raise DomainError("Hạn mức hoặc tiền tệ không khớp provider.")
        try:
            expiration = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise DomainError("Ngày hết hạn grant không hợp lệ.") from exc
        if expiration.tzinfo is None or expiration <= datetime.now(timezone.utc):
            raise DomainError("Grant phải có thời hạn tương lai và múi giờ.")
        item = {"id": uid(), "project_id": project_id, "provider_kind": self.provider.capabilities.provider_kind,
                "currency": currency, "max_micro": max_micro, "reserved_micro": 0, "spent_micro": 0,
                "expires_at": expiration.isoformat(), "created_at": now()}
        with self.studio.db.transaction() as conn:
            conn.execute("INSERT INTO video_grants VALUES(:id,:project_id,:provider_kind,:currency,:max_micro,:reserved_micro,:spent_micro,:expires_at,:created_at)", item)
            self.studio.audit(conn, project_id, "video.grant_created", item["id"], {"currency": currency, "max_micro": max_micro})
        return item

    def grants(self, project_id: str) -> list[dict]:
        self.studio.project(project_id)
        with self.studio.db.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM video_grants WHERE project_id=? ORDER BY created_at DESC", (project_id,))]

    def _asset(self, conn, project_id: str, asset_id: str, kind: str) -> dict:
        row = require(conn.execute("SELECT * FROM asset_versions WHERE id=? AND project_id=? AND kind=?",
                                   (asset_id, project_id, kind)).fetchone(), f"{kind} asset version")
        latest = conn.execute("SELECT MAX(version) FROM asset_versions WHERE project_id=? AND registry_id=?",
                              (project_id, row["registry_id"])).fetchone()[0]
        if row["version"] != latest:
            raise DomainError(f"{kind} asset version đã STALE.", 409)
        return dict(row)

    def _reference_item(self, conn, shot, item: dict, neighbor: str) -> dict:
        if not isinstance(item, dict) or set(item) != {"shot_id", "source_take_id", "frame", "relative_path", "sha256"}:
            raise DomainError(f"{neighbor}: cần shot, final take đã duyệt, frame và file SHA-256.")
        if type(item["frame"]) is not int or any(not isinstance(item[k], str) or not item[k] for k in ("shot_id", "source_take_id", "relative_path", "sha256")):
            raise DomainError(f"{neighbor}: ID/path/checksum phải là chuỗi và frame là số nguyên.")
        adjacent = self._shot(conn, item["shot_id"])
        if adjacent["project_id"] != shot["project_id"] or adjacent["episode_id"] != shot["episode_id"] or adjacent["ordinal"] != shot["ordinal"] + (-1 if neighbor == "previous" else 1):
            raise DomainError(f"{neighbor}: không phải cảnh liền kề trong cùng tập.", 409)
        take = require(conn.execute("SELECT * FROM video_takes WHERE id=? AND shot_id=? AND stage='FINAL'", (item["source_take_id"], adjacent["id"])).fetchone(), "neighbor final take")
        if take["validity"] != "CURRENT" or take["review"] != "APPROVED" or take["shot_revision"] != adjacent["current_revision"]:
            raise DomainError(f"{neighbor}: final take chưa duyệt hoặc đã cũ.", 409)
        self._checked_file(shot["project_id"], take["relative_path"], take["sha256"])
        manifest = json.loads(conn.execute("SELECT manifest_json FROM shot_revisions WHERE shot_id=? AND revision=?",
                                           (adjacent["id"], adjacent["current_revision"])).fetchone()[0])
        expected_frame = manifest["timeline"]["out_exclusive"] - 1 if neighbor == "previous" else manifest["timeline"]["in"]
        if type(item["frame"]) is not int or item["frame"] != expected_frame:
            raise DomainError(f"{neighbor}: frame phải là {expected_frame}.", 409)
        relative, _ = self._checked_file(shot["project_id"], item["relative_path"], item["sha256"])
        return {"shot_id": adjacent["id"], "shot_revision": adjacent["current_revision"],
                "source_take_id": take["id"], "source_sha256": take["sha256"], "source_provenance": take["provenance"],
                "frame": expected_frame, "relative_path": relative, "sha256": item["sha256"].lower(),
                "frame_extraction": "USER_REVIEWED_IMAGE"}

    def register_asset_reference(self, project_id: str, asset_version_id: str, relative_path: str, sha256: str) -> dict:
        relative, path = self._checked_file(project_id, relative_path, sha256)
        if path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            raise DomainError("Ảnh tham chiếu phải là PNG/JPEG.")
        with self.studio.db.transaction() as conn:
            asset = require(conn.execute("SELECT * FROM asset_versions WHERE id=? AND project_id=?", (asset_version_id, project_id)).fetchone(), "reference asset version")
            if asset["kind"] not in ("character", "environment", "style_reference"):
                raise DomainError("Chỉ nhân vật/bối cảnh/phong cách có ảnh tham chiếu.")
            self._asset(conn, project_id, asset_version_id, asset["kind"])
            version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM media_asset_references WHERE asset_version_id=?", (asset_version_id,)).fetchone()[0]
            item = {"id": uid(), "project_id": project_id, "asset_version_id": asset_version_id, "version": version,
                    "relative_path": relative, "sha256": sha256.lower(), "review": "APPROVED", "created_at": now()}
            conn.execute("INSERT INTO media_asset_references VALUES(:id,:project_id,:asset_version_id,:version,:relative_path,:sha256,:review,:created_at)", item)
            for row in conn.execute("SELECT shot_id,references_json FROM video_reference_packages WHERE project_id=? AND validity='CURRENT'", (project_id,)).fetchall():
                package = json.loads(row["references_json"])
                if any(image["asset_version_id"] == asset_version_id for image in package["asset_images"]):
                    conn.execute("UPDATE video_reference_packages SET validity='STALE' WHERE shot_id=?", (row["shot_id"],))
                    conn.execute("UPDATE video_takes SET validity='STALE' WHERE shot_id=? AND route IN ('SEEDANCE','HYBRID') AND validity='CURRENT'", (row["shot_id"],))
                    self._stale_picture(conn, row["shot_id"])
            conn.execute("INSERT INTO approvals(id,project_id,scope,scope_id,exact_version,decision_json,actor,created_at) VALUES(?,?,'media_asset_reference',?,?,?,'local_user',?)",
                         (uid(), project_id, item["id"], version, canonical({"asset_version_id": asset_version_id, "sha256": sha256.lower()}), now()))
            self.studio.audit(conn, project_id, "media.asset_reference_approved", item["id"], {"asset_version_id": asset_version_id, "version": version})
            return item

    def asset_references(self, project_id: str) -> list[dict]:
        self.studio.project(project_id)
        with self.studio.db.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM media_asset_references WHERE project_id=? ORDER BY created_at DESC", (project_id,))]

    def create_reference_package(self, shot_id: str, expected_shot_revision: int, refs: dict) -> dict:
        if not isinstance(refs, dict) or set(refs) != {"previous", "next", "character_asset_version_ids", "environment_asset_version_id", "style_asset_version_id", "control_video_take_id"}:
            raise DomainError("Reference package cần previous/next, character, environment, style và control video (có thể null).")
        with self.studio.db.transaction() as conn:
            shot = self._shot(conn, shot_id)
            route = self._approval(conn, shot)
            if shot["current_revision"] != expected_shot_revision or route not in ("SEEDANCE", "HYBRID"):
                raise DomainError("Cần route SEEDANCE/HYBRID duyệt đúng shot revision.", 409)
            characters = refs["character_asset_version_ids"]
            if not isinstance(characters, list) or not characters or any(not isinstance(c, str) or not c for c in characters) or len(set(characters)) != len(characters):
                raise DomainError("Cần danh sách character version không trùng.")
            if any(not isinstance(refs[k], str) or not refs[k] for k in ("environment_asset_version_id", "style_asset_version_id")) or (refs["control_video_take_id"] is not None and not isinstance(refs["control_video_take_id"], str)):
                raise DomainError("Environment/style/control ID phải là chuỗi registry version.")
            pinned = {"characters": [self._asset(conn, shot["project_id"], a, "character")["id"] for a in characters],
                      "environment": self._asset(conn, shot["project_id"], refs["environment_asset_version_id"], "environment")["id"],
                      "style": self._asset(conn, shot["project_id"], refs["style_asset_version_id"], "style_reference")["id"]}
            manifest_row = conn.execute("SELECT resolved_registry_json,manifest_json FROM shot_revisions WHERE shot_id=? AND revision=?", (shot_id, expected_shot_revision)).fetchone()
            manifest, registry_pins = json.loads(manifest_row["manifest_json"]), json.loads(manifest_row["resolved_registry_json"])
            if pinned["environment"] != registry_pins[manifest["environment_id"]]["asset_version_id"] or any(registry_pins[p["character_id"]]["asset_version_id"] not in pinned["characters"] for p in manifest["performances"]):
                raise DomainError("Nhân vật/bối cảnh reference không khớp registry version đã pin của shot.", 409)
            asset_images = []
            for asset_id in [*pinned["characters"], pinned["environment"], pinned["style"]]:
                image = require(conn.execute("SELECT * FROM media_asset_references WHERE asset_version_id=? AND review='APPROVED' ORDER BY version DESC LIMIT 1", (asset_id,)).fetchone(), "approved asset reference image")
                self._checked_file(shot["project_id"], image["relative_path"], image["sha256"])
                asset_images.append(dict(image))
            previous = self._reference_item(conn, shot, refs["previous"], "previous") if refs["previous"] else None
            following = self._reference_item(conn, shot, refs["next"], "next") if refs["next"] else None
            previous_shot = conn.execute("SELECT id FROM shots WHERE episode_id=? AND ordinal=?", (shot["episode_id"], shot["ordinal"] - 1)).fetchone()
            if not previous and previous_shot:
                raise DomainError("Cảnh không phải đầu tập cần previous final frame đã khóa.", 409)
            next_shot = conn.execute("SELECT id FROM shots WHERE episode_id=? AND ordinal=?", (shot["episode_id"], shot["ordinal"] + 1)).fetchone()
            if next_shot and not following:
                raise DomainError("Cảnh không phải cuối tập cần next first frame đã khóa.", 409)
            control = None
            if route == "HYBRID":
                if not refs["control_video_take_id"]:
                    raise DomainError("HYBRID cần UE control video đã duyệt.", 409)
                row = require(conn.execute("SELECT * FROM video_takes WHERE id=? AND shot_id=?", (refs["control_video_take_id"], shot_id)).fetchone(), "UE control take")
                if row["route"] != "UNREAL" or row["stage"] != "FINAL" or row["review"] != "APPROVED" or row["validity"] != "CURRENT" or row["shot_revision"] != expected_shot_revision or row["provenance"] != "UE_MRQ_VERIFIED":
                    raise DomainError("UE control video chưa có receipt MRQ và review current.", 409)
                self._checked_file(shot["project_id"], row["relative_path"], row["sha256"])
                control = {"take_id": row["id"], "sha256": row["sha256"], "relative_path": row["relative_path"]}
            elif refs["control_video_take_id"] is not None:
                raise DomainError("SEEDANCE không nhận control video; chọn HYBRID và duyệt route.")
            package = {"previous": previous, "next": following, "assets": pinned, "asset_images": asset_images, "control_video": control}
            old = conn.execute("SELECT * FROM video_reference_packages WHERE shot_id=? AND shot_revision=? AND route=? AND input_hash=? AND validity='CURRENT'", (shot_id, expected_shot_revision, route, digest(package))).fetchone()
            if old:
                return dict(old, references=package)
            version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM video_reference_packages WHERE shot_id=?", (shot_id,)).fetchone()[0]
            item = {"id": uid(), "project_id": shot["project_id"], "shot_id": shot_id,
                    "shot_revision": expected_shot_revision, "version": version, "route": route,
                    "references_json": canonical(package), "input_hash": digest(package), "validity": "CURRENT", "created_at": now()}
            conn.execute("UPDATE video_reference_packages SET validity='STALE' WHERE shot_id=? AND validity='CURRENT'", (shot_id,))
            conn.execute("UPDATE video_takes SET validity='STALE' WHERE shot_id=? AND route IN ('SEEDANCE','HYBRID') AND validity='CURRENT'", (shot_id,))
            self._stale_picture(conn, shot_id)
            conn.execute("INSERT INTO video_reference_packages VALUES(:id,:project_id,:shot_id,:shot_revision,:version,:route,:references_json,:input_hash,:validity,:created_at)", item)
            conn.execute("INSERT INTO approvals(id,project_id,scope,scope_id,exact_version,decision_json,actor,created_at) VALUES(?,?,'video_reference',?,?,?,'local_user',?)",
                         (uid(), shot["project_id"], item["id"], version, canonical({"input_hash": item["input_hash"]}), now()))
            self.studio.audit(conn, shot["project_id"], "video.references_created", item["id"], {"version": version, "input_hash": item["input_hash"]})
            return dict(item, references=package)

    def references(self, shot_id: str) -> list[dict]:
        self.studio.shot(shot_id)
        with self.studio.db.connect() as conn:
            return [dict(row, references=json.loads(row["references_json"])) for row in conn.execute(
                "SELECT * FROM video_reference_packages WHERE shot_id=? ORDER BY version DESC", (shot_id,))]

    def preflight(self, shot_id: str, stage: str, grant_id: str | None = None, *, reservation_credit: int = 0) -> dict:
        if stage not in ("DRAFT", "FINAL"):
            raise DomainError("Video stage phải là DRAFT hoặc FINAL.")
        shot = self.studio.shot(shot_id)
        cap = self.provider.capabilities
        resolution = "480p" if stage == "DRAFT" else "720p"
        blockers = []
        with self.studio.db.connect() as conn:
            row = self._shot(conn, shot_id)
            route = self._approval(conn, row)
            if route not in ("SEEDANCE", "HYBRID"):
                blockers.append("Route SEEDANCE/HYBRID chưa được người dùng duyệt đúng revision")
            state = conn.execute("SELECT * FROM subsystem_states WHERE shot_id=? AND subsystem='render'", (shot_id,)).fetchone()
            if state and (state["mode"] == "MANUAL" or state["lock_state"] == "LOCKED" or (state["observed_fingerprint"] and state["observed_fingerprint"] != state["synced_fingerprint"])):
                blockers.append("Render MANUAL/LOCKED hoặc có chỉnh ngoài app")
            refs = conn.execute("SELECT * FROM video_reference_packages WHERE shot_id=? AND validity='CURRENT' ORDER BY version DESC LIMIT 1", (shot_id,)).fetchone()
            if not refs or refs["shot_revision"] != row["current_revision"] or refs["route"] != route:
                blockers.append("Thiếu reference package current đúng route/revision")
            elif not self._references_current(conn, row, refs):
                blockers.append("Reference file, asset hoặc neighbor Picture Lock đã đổi")
            draft = None
            if stage == "FINAL":
                draft = conn.execute("SELECT t.* FROM video_selections s JOIN video_takes t ON t.id=s.take_id WHERE s.shot_id=? AND s.stage='DRAFT'", (shot_id,)).fetchone()
                if not draft or draft["review"] != "APPROVED" or draft["validity"] != "CURRENT" or draft["shot_revision"] != row["current_revision"]:
                    blockers.append("Cần chọn và duyệt draft current trước final")
                elif not self._file_current(row["project_id"], draft):
                    blockers.append("Draft đã chọn thiếu hoặc checksum đổi")
            if stage == "DRAFT" and resolution not in cap.draft_resolutions or stage == "FINAL" and resolution not in cap.final_resolutions:
                blockers.append(f"Model {cap.model} không hỗ trợ preset {stage} {resolution}; cần chọn cấu hình khác để duyệt")
            if route == "SEEDANCE" and not cap.first_last_frames:
                blockers.append("Model không hỗ trợ first/last frame")
            if route == "HYBRID" and not cap.reference_video:
                blockers.append("Model không hỗ trợ reference control video")
            if stage == "FINAL" and not cap.draft_to_final:
                blockers.append("Model không hỗ trợ tạo final từ selected draft")
            if not cap.operational:
                blockers.append("Seedance chưa có account, upload, giá và kết quả live được kiểm chứng")
            grant = conn.execute("SELECT * FROM video_grants WHERE id=? AND project_id=? AND provider_kind=?", (grant_id, row["project_id"], cap.provider_kind)).fetchone() if grant_id else None
            if not grant or grant["currency"] != cap.currency or datetime.fromisoformat(grant["expires_at"]) <= datetime.now(timezone.utc):
                blockers.append("Cần video budget grant còn hạn đúng provider/currency")
            estimate = None
            request = self._request(shot, route, stage, resolution, refs, draft) if refs and route in ("SEEDANCE", "HYBRID") and (stage == "DRAFT" or draft) else None
            if cap.operational and not blockers:
                estimate = self.provider.estimate_micro(request)
                if type(estimate) is not int or estimate < 0 or grant["max_micro"] - grant["reserved_micro"] - grant["spent_micro"] + reservation_credit < estimate:
                    blockers.append("Budget video không đủ hoặc estimate không hợp lệ")
        return {"allowed": not blockers, "blockers": blockers, "route": route, "resolution": resolution,
                "provider_kind": cap.provider_kind, "estimated_micro": estimate, "currency": cap.currency,
                "reference_package_id": refs["id"] if refs else None, "selected_draft_id": draft["id"] if draft else None,
                "input_request": request}

    def _file_current(self, project_id: str, record) -> bool:
        try:
            self._checked_file(project_id, record["relative_path"], record["sha256"])
            return True
        except DomainError:
            return False

    def _references_current(self, conn, shot, refs) -> bool:
        package = json.loads(refs["references_json"])
        try:
            for kind, ids in (("character", package["assets"]["characters"]), ("environment", [package["assets"]["environment"]]),
                              ("style_reference", [package["assets"]["style"]])):
                for asset_id in ids:
                    self._asset(conn, shot["project_id"], asset_id, kind)
            for side in ("previous", "next"):
                item = package[side]
                if item:
                    self._reference_item(conn, shot, {key: item[key] for key in ("shot_id", "source_take_id", "frame", "relative_path", "sha256")}, side)
            for image in package["asset_images"]:
                latest = conn.execute("SELECT id FROM media_asset_references WHERE asset_version_id=? AND review='APPROVED' ORDER BY version DESC LIMIT 1", (image["asset_version_id"],)).fetchone()
                if not latest or latest[0] != image["id"]:
                    return False
                self._checked_file(shot["project_id"], image["relative_path"], image["sha256"])
            control = package["control_video"]
            if control:
                row = conn.execute("SELECT * FROM video_takes WHERE id=?", (control["take_id"],)).fetchone()
                if not row or row["shot_revision"] != shot["current_revision"] or row["review"] != "APPROVED" or row["validity"] != "CURRENT" or row["provenance"] != "UE_MRQ_VERIFIED" or not self._file_current(shot["project_id"], row):
                    return False
            return True
        except (DomainError, KeyError, TypeError):
            return False

    def _request(self, shot: dict, route: str, stage: str, resolution: str, refs, draft) -> dict:
        return {"schema_version": "p6.video-request.1", "shot_id": shot["id"], "shot_revision": shot["current_revision"],
                "input_hash": shot["input_hash"], "fps": shot["manifest"]["fps"], "timeline": shot["manifest"]["timeline"],
                "route": route, "stage": stage, "resolution": resolution, "reference_package_id": refs["id"],
                "reference_hash": refs["input_hash"], "selected_draft_id": draft["id"] if draft else None,
                "selected_draft_sha256": draft["sha256"] if draft else None,
                "selected_draft_provider_request_id": draft["provider_request_id"] if draft else None,
                "reference_package": json.loads(refs["references_json"]),
                "render_state_revision": next(s["state_revision"] for s in shot["states"] if s["subsystem"] == "render"),
                "provider_profile_hash": digest(asdict(self.provider.capabilities)),
                "generate_audio": False}

    def enqueue(self, shot_id: str, stage: str, client_request_id: str, grant_id: str) -> dict:
        if not isinstance(client_request_id, str) or not client_request_id.strip() or len(client_request_id) > 128:
            raise DomainError("Cần client request ID hợp lệ.")
        check = self.preflight(shot_id, stage, grant_id)
        if check["input_request"]:
            input_hash = digest(check["input_request"])
            operation_id = digest({"client_request_id": client_request_id, "input_hash": input_hash})
            with self.studio.db.connect() as conn:
                existing = conn.execute("SELECT * FROM video_jobs WHERE operation_id=?", (operation_id,)).fetchone()
                if existing:
                    return self._job(conn, existing)
                pending = conn.execute("SELECT * FROM video_jobs WHERE provider_kind=? AND input_hash=? AND state IN ('QUEUED','RUNNING','PENDING','RECONCILING') ORDER BY created_at DESC LIMIT 1", (self.provider.capabilities.provider_kind, input_hash)).fetchone()
                if pending:
                    return self._job(conn, pending)
        if not check["allowed"]:
            raise DomainError("; ".join(check["blockers"]), 409)
        shot = self.studio.shot(shot_id)
        with self.studio.db.transaction() as conn:
            row = self._shot(conn, shot_id)
            if row["current_revision"] != shot["current_revision"] or self._approval(conn, row) != check["route"]:
                raise DomainError("Shot/route đổi trước khi xếp việc.", 409)
            state = conn.execute("SELECT * FROM subsystem_states WHERE shot_id=? AND subsystem='render'", (shot_id,)).fetchone()
            if not state or state["mode"] != "AUTO" or state["lock_state"] != "UNLOCKED" or state["state_revision"] != check["input_request"]["render_state_revision"] or (state["observed_fingerprint"] and state["observed_fingerprint"] != state["synced_fingerprint"]):
                raise DomainError("Render lock/revision đổi trước enqueue.", 409)
            refs = require(conn.execute("SELECT * FROM video_reference_packages WHERE id=? AND validity='CURRENT'", (check["reference_package_id"],)).fetchone(), "reference package")
            if not self._references_current(conn, row, refs):
                raise DomainError("Reference đổi trước khi xếp việc.", 409)
            draft = conn.execute("SELECT * FROM video_takes WHERE id=?", (check["selected_draft_id"],)).fetchone() if check["selected_draft_id"] else None
            if draft:
                selection = conn.execute("SELECT take_id FROM video_selections WHERE shot_id=? AND stage='DRAFT'", (shot_id,)).fetchone()
                if not selection or selection[0] != draft["id"] or draft["review"] != "APPROVED" or draft["validity"] != "CURRENT" or not self._file_current(row["project_id"], draft):
                    raise DomainError("Selected draft đổi trước enqueue.", 409)
            request = self._request(shot, check["route"], stage, check["resolution"], refs, draft)
            input_hash = digest(request)
            operation_id = digest({"client_request_id": client_request_id, "input_hash": input_hash})
            existing = conn.execute("SELECT * FROM video_jobs WHERE operation_id=?", (operation_id,)).fetchone()
            if existing:
                return self._job(conn, existing)
            pending = conn.execute("SELECT * FROM video_jobs WHERE shot_id=? AND stage=? AND state IN ('QUEUED','RUNNING','PENDING','RECONCILING') ORDER BY created_at DESC LIMIT 1",
                                   (shot_id, stage)).fetchone()
            if pending:
                if pending["input_hash"] != input_hash:
                    raise DomainError("Shot còn request chưa rõ kết quả; đối soát trước khi tạo input mới có phí.", 409)
                return self._job(conn, pending)
            estimate = self.provider.estimate_micro(request)
            if type(estimate) is not int or estimate < 0:
                raise DomainError("Estimate provider không hợp lệ.", 409)
            grant = require(conn.execute("SELECT * FROM video_grants WHERE id=? AND project_id=?", (grant_id, row["project_id"])).fetchone(), "video grant")
            if grant["provider_kind"] != self.provider.capabilities.provider_kind or grant["currency"] != self.provider.capabilities.currency or grant["max_micro"] - grant["reserved_micro"] - grant["spent_micro"] < estimate or datetime.fromisoformat(grant["expires_at"]) <= datetime.now(timezone.utc):
                raise DomainError("Video grant không còn đủ hạn mức.", 409)
            conn.execute("UPDATE video_grants SET reserved_micro=reserved_micro+? WHERE id=?", (estimate, grant_id))
            request["operation_id"] = operation_id
            item = {"id": uid(), "project_id": row["project_id"], "shot_id": shot_id, "shot_revision": row["current_revision"],
                    "route": check["route"], "stage": stage, "provider_kind": self.provider.capabilities.provider_kind,
                    "operation_id": operation_id, "input_hash": input_hash, "request_json": canonical(request),
                    "state": "QUEUED", "attempt": 0, "grant_id": grant_id, "estimated_micro": estimate,
                    "actual_micro": None, "provider_request_id": None, "error": None, "created_at": now(), "updated_at": now()}
            conn.execute("INSERT INTO video_jobs VALUES(:id,:project_id,:shot_id,:shot_revision,:route,:stage,:provider_kind,:operation_id,:input_hash,:request_json,:state,:attempt,:grant_id,:estimated_micro,:actual_micro,:provider_request_id,:error,:created_at,:updated_at)", item)
            self.studio.audit(conn, row["project_id"], "video.queued", item["id"], {"operation_id": operation_id, "input_hash": input_hash, "estimate_micro": estimate})
            return self._job(conn, item)

    def jobs(self, project_id: str) -> list[dict]:
        self.studio.project(project_id)
        with self.studio.db.connect() as conn:
            return [self._job(conn, row) for row in conn.execute("SELECT * FROM video_jobs WHERE project_id=? ORDER BY created_at DESC", (project_id,))]

    def _job(self, conn, row) -> dict:
        item = dict(row)
        item.pop("request_json", None)
        item["attempts"] = [dict(a) for a in conn.execute("SELECT * FROM video_attempts WHERE job_id=? ORDER BY attempt", (item["id"],))]
        receipt = conn.execute("SELECT output_json FROM receipts WHERE operation_id=?", (item["operation_id"],)).fetchone()
        item["receipt"] = json.loads(receipt[0]) if receipt else None
        return item

    def job(self, job_id: str) -> dict:
        with self.studio.db.connect() as conn:
            return self._job(conn, require(conn.execute("SELECT * FROM video_jobs WHERE id=?", (job_id,)).fetchone(), "video job"))

    def process_next(self) -> dict | None:
        with self.studio.db.transaction() as conn:
            row = conn.execute("SELECT * FROM video_jobs WHERE state='QUEUED' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            job = dict(row)
            conn.execute("UPDATE video_jobs SET state='RUNNING',attempt=attempt+1,updated_at=? WHERE id=?", (now(), job["id"]))
            conn.execute("INSERT INTO video_attempts(id,job_id,attempt,state,started_at) VALUES(?,?,?,'RUNNING',?)", (uid(), job["id"], job["attempt"] + 1, now()))
        try:
            check = self.preflight(job["shot_id"], job["stage"], job["grant_id"], reservation_credit=job["estimated_micro"])
            if not check["allowed"] or not check["input_request"] or digest(check["input_request"]) != job["input_hash"] or self.provider.capabilities.provider_kind != job["provider_kind"]:
                raise DomainError("; ".join(check["blockers"]) or "Nguồn video hoặc selected draft thay đổi trước submit.", 409)
        except DomainError as exc:
            with self.studio.db.transaction() as conn:
                conn.execute("UPDATE video_jobs SET state='BLOCKED',error=?,updated_at=? WHERE id=?", (str(exc), now(), job["id"]))
                conn.execute("UPDATE video_attempts SET state='BLOCKED',error=?,ended_at=? WHERE job_id=? AND attempt=?", (str(exc), now(), job["id"], job["attempt"] + 1))
                conn.execute("UPDATE video_grants SET reserved_micro=reserved_micro-? WHERE id=?", (job["estimated_micro"], job["grant_id"]))
            return self.job(job["id"])
        try:
            submitted = self.provider.submit(json.loads(job["request_json"]))
            if not submitted.provider_request_id:
                raise RuntimeError("Provider không trả task ID; outcome không rõ")
        except Exception:
            with self.studio.db.transaction() as conn:
                conn.execute("UPDATE video_jobs SET state='RECONCILING',error='Submit outcome không rõ; cần đối soát thủ công, không POST lại.',updated_at=? WHERE id=?", (now(), job["id"]))
                conn.execute("UPDATE video_attempts SET state='RECONCILING',error='submit uncertain',ended_at=? WHERE job_id=? AND attempt=?", (now(), job["id"], job["attempt"] + 1))
            return self.job(job["id"])
        with self.studio.db.transaction() as conn:
            conn.execute("UPDATE video_jobs SET state='PENDING',provider_request_id=?,updated_at=? WHERE id=?", (submitted.provider_request_id, now(), job["id"]))
            conn.execute("UPDATE video_attempts SET state='PENDING',provider_request_id=? WHERE job_id=? AND attempt=?", (submitted.provider_request_id, job["id"], job["attempt"] + 1))
            self.studio.audit(conn, job["project_id"], "video.submitted", job["id"], {"provider_request_id": submitted.provider_request_id})
        return self.job(job["id"])

    def reconcile(self, job_id: str) -> dict:
        job = self.job(job_id)
        if job["state"] == "COMPLETED":
            return job
        if job["state"] not in ("PENDING", "RECONCILING"):
            raise DomainError("Chỉ đối soát job PENDING/RECONCILING.", 409)
        if not job["provider_request_id"]:
            raise DomainError("Chưa có provider task ID; cần tra thủ công theo operation ID. Không tự POST lại.", 409)
        with self.studio.db.connect() as conn:
            request = json.loads(conn.execute("SELECT request_json FROM video_jobs WHERE id=?", (job_id,)).fetchone()[0])
        if self.provider.capabilities.provider_kind != job["provider_kind"] or digest(asdict(self.provider.capabilities)) != request["provider_profile_hash"]:
            raise DomainError("Provider/profile đã đổi; phục hồi đúng profile trước đối soát task cũ.", 409)
        try:
            result = self.provider.poll(job["provider_request_id"])
        except Exception:
            with self.studio.db.transaction() as conn:
                conn.execute("UPDATE video_jobs SET state='RECONCILING',error='Polling timeout/lỗi; giữ task ID và reservation, không POST lại.',updated_at=? WHERE id=? AND state IN ('PENDING','RECONCILING')", (now(), job_id))
            return self.job(job_id)
        if result.state == "PENDING":
            with self.studio.db.transaction() as conn:
                conn.execute("UPDATE video_jobs SET state='PENDING',updated_at=? WHERE id=? AND state IN ('PENDING','RECONCILING')", (now(), job_id))
            return self.job(job_id)
        if result.state != "SUCCEEDED":
            with self.studio.db.transaction() as conn:
                conn.execute("UPDATE video_jobs SET state='RECONCILING',error='Provider báo lỗi; kiểm tra phí trước khi đóng job.',updated_at=? WHERE id=? AND state IN ('PENDING','RECONCILING')", (now(), job_id))
            return self.job(job_id)
        if any(v is None for v in (result.relative_path, result.sha256, result.duration_frames, result.fps_num, result.fps_den, result.actual_micro, result.resolution)):
            raise DomainError("Provider output thiếu metadata, cần đối soát.", 409)
        relative, path = self._checked_file(job["project_id"], result.relative_path, result.sha256)
        if path.suffix.lower() not in (".mp4", ".mov"):
            raise DomainError("Provider output phải là video MP4/MOV.", 409)
        shot = self.studio.shot(job["shot_id"])
        with self.studio.db.connect() as conn:
            request = json.loads(conn.execute("SELECT request_json FROM video_jobs WHERE id=?", (job_id,)).fetchone()[0])
        if type(result.duration_frames) is not int or result.duration_frames <= 0 or type(result.fps_num) is not int or type(result.fps_den) is not int or result.fps_num <= 0 or result.fps_den <= 0 or type(result.actual_micro) is not int or result.actual_micro < 0:
            raise DomainError("Metadata duration/FPS/cost provider không hợp lệ.", 409)
        if result.actual_micro > job["estimated_micro"]:
            raise DomainError("Chi phí thực vượt reservation; cần duyệt/đối soát thủ công.", 409)
        if result.resolution != request["resolution"] or (result.fps_num, result.fps_den) != (request["fps"]["num"], request["fps"]["den"]) or result.duration_frames != request["timeline"]["out_exclusive"] - request["timeline"]["in"]:
            raise DomainError("Video provider sai FPS/duration; giữ PENDING để review, không nhận thành công.", 409)
        with self.studio.db.transaction() as conn:
            current = require(conn.execute("SELECT * FROM video_jobs WHERE id=?", (job_id,)).fetchone(), "video job")
            if current["state"] == "COMPLETED":
                return self._job(conn, current)
            if current["provider_request_id"] != job["provider_request_id"] or current["state"] not in ("PENDING", "RECONCILING"):
                raise DomainError("Provider event cũ hoặc không khớp task ID.", 409)
            old_path = conn.execute("SELECT id FROM video_takes WHERE project_id=? AND relative_path=?", (job["project_id"], relative)).fetchone()
            if old_path:
                raise DomainError("Take mới phải có file output mới; không ghi đè draft/final cũ.", 409)
            version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM video_takes WHERE shot_id=? AND stage=?", (job["shot_id"], job["stage"])).fetchone()[0]
            take_id = uid()
            conn.execute("INSERT INTO video_takes(id,job_id,project_id,shot_id,shot_revision,stage,route,version,relative_path,sha256,duration_frames,fps_num,fps_den,provenance,provider_request_id,actual_micro,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (take_id, job_id, job["project_id"], job["shot_id"], job["shot_revision"], job["stage"], job["route"], version,
                          relative, result.sha256.lower(), result.duration_frames, result.fps_num, result.fps_den,
                          "SEEDANCE_LIVE_VERIFIED" if getattr(self.provider, "integration_verified", False) else ("FAKE_VIDEO_CONTRACT" if job["provider_kind"].startswith("FAKE_") else "PROVIDER_OUTPUT_UNREVIEWED"),
                          job["provider_request_id"], result.actual_micro, now()))
            conn.execute("UPDATE video_jobs SET state='COMPLETED',actual_micro=?,updated_at=? WHERE id=?", (result.actual_micro, now(), job_id))
            conn.execute("UPDATE video_attempts SET state='COMPLETED',ended_at=? WHERE job_id=? AND attempt=?", (now(), job_id, job["attempt"]))
            conn.execute("UPDATE video_grants SET reserved_micro=reserved_micro-?,spent_micro=spent_micro+? WHERE id=?", (job["estimated_micro"], result.actual_micro, job["grant_id"]))
            fresh_shot = self._shot(conn, job["shot_id"])
            fresh_hash = conn.execute("SELECT input_hash FROM shot_revisions WHERE shot_id=? AND revision=?", (job["shot_id"], fresh_shot["current_revision"])).fetchone()[0]
            state = conn.execute("SELECT state_revision FROM subsystem_states WHERE shot_id=? AND subsystem='render'", (job["shot_id"],)).fetchone()
            draft_selection = conn.execute("SELECT take_id FROM video_selections WHERE shot_id=? AND stage='DRAFT'", (job["shot_id"],)).fetchone()
            refs = conn.execute("SELECT * FROM video_reference_packages WHERE id=? AND validity='CURRENT'", (request["reference_package_id"],)).fetchone()
            if fresh_shot["current_revision"] != job["shot_revision"] or fresh_hash != request["input_hash"] or self._approval(conn, fresh_shot) != job["route"] or not state or state[0] != request["render_state_revision"] or not refs or not self._references_current(conn, fresh_shot, refs) or (job["stage"] == "FINAL" and (not draft_selection or draft_selection[0] != request["selected_draft_id"])):
                conn.execute("UPDATE video_takes SET validity='STALE' WHERE id=?", (take_id,))
            receipt = {"schema_version": "p6.video-result.1", "operation_id": job["operation_id"], "input_hash": job["input_hash"],
                       "provider_kind": job["provider_kind"], "provider_request_id": job["provider_request_id"], "take_id": take_id,
                       "stage": job["stage"], "version": version, "relative_path": relative, "sha256": result.sha256.lower(),
                       "duration_frames": result.duration_frames, "fps": {"num": result.fps_num, "den": result.fps_den},
                       "resolution": result.resolution, "actual_micro": result.actual_micro,
                       "currency": self.provider.capabilities.currency, "review": "NEEDS_REVIEW",
                       "integration_verified": bool(getattr(self.provider, "integration_verified", False))}
            conn.execute("INSERT INTO receipts(id,operation_id,input_hash,status,output_json,created_at) VALUES(?,?,?,'COMPLETED',?,?)", (uid(), job["operation_id"], job["input_hash"], canonical(receipt), now()))
            self.studio.audit(conn, job["project_id"], "video.take_created", take_id, {"version": version, "provider_request_id": job["provider_request_id"], "actual_micro": result.actual_micro})
        return self.job(job_id)

    def poll_next(self) -> dict | None:
        with self.studio.db.connect() as conn:
            row = conn.execute("SELECT id FROM video_jobs WHERE state='PENDING' AND julianday('now')-julianday(updated_at)>5.0/86400 ORDER BY updated_at LIMIT 1").fetchone()
        return self.reconcile(row[0]) if row else None

    def takes(self, shot_id: str) -> list[dict]:
        self.studio.shot(shot_id)
        with self.studio.db.connect() as conn:
            return [dict(row, review_details=json.loads(row["review_json"]) if row["review_json"] else None) for row in conn.execute(
                "SELECT * FROM video_takes WHERE shot_id=? ORDER BY created_at DESC", (shot_id,))]

    def review_take(self, take_id: str, expected_version: int, decision: str, checks: dict) -> dict:
        if decision not in ("APPROVED", "REJECTED") or not isinstance(checks, dict) or set(checks) != {"identity", "costume", "camera", "timing", "lip_sync"} or any(type(v) is not bool for v in checks.values()):
            raise DomainError("Cần quyết định và checklist identity/costume/camera/timing/lip sync.")
        if decision == "APPROVED" and not all(checks.values()):
            raise DomainError("Take có drift phải REJECTED; không thể duyệt.", 409)
        with self.studio.db.transaction() as conn:
            take = require(conn.execute("SELECT * FROM video_takes WHERE id=?", (take_id,)).fetchone(), "video take")
            shot = self._shot(conn, take["shot_id"])
            if take["version"] != expected_version or take["review"] != "NEEDS_REVIEW" or take["shot_revision"] != shot["current_revision"] or take["validity"] != "CURRENT":
                raise DomainError("Take version đã đổi hoặc không current.", 409)
            self._checked_file(take["project_id"], take["relative_path"], take["sha256"])
            conn.execute("UPDATE video_takes SET review=?,review_json=? WHERE id=?", (decision, canonical(checks), take_id))
            conn.execute("INSERT INTO approvals(id,project_id,scope,scope_id,exact_version,decision_json,actor,created_at) VALUES(?,?,'video_take',?,?,?,'local_user',?)",
                         (uid(), take["project_id"], take_id, expected_version, canonical({"decision": decision, "checks": checks, "sha256": take["sha256"]}), now()))
            self.studio.audit(conn, take["project_id"], "video.take_reviewed", take_id, {"decision": decision, "checks": checks})
            return dict(conn.execute("SELECT * FROM video_takes WHERE id=?", (take_id,)).fetchone())

    def select_take(self, take_id: str, expected_version: int) -> dict:
        with self.studio.db.transaction() as conn:
            take = require(conn.execute("SELECT * FROM video_takes WHERE id=?", (take_id,)).fetchone(), "video take")
            shot = self._shot(conn, take["shot_id"])
            if take["version"] != expected_version or take["review"] != "APPROVED" or take["validity"] != "CURRENT" or take["shot_revision"] != shot["current_revision"]:
                raise DomainError("Chỉ chọn take đã duyệt, current, đúng shot revision.", 409)
            if self._approval(conn, shot) != take["route"]:
                raise DomainError("Take không khớp route C5; cần duyệt UE fallback hoặc route mới.", 409)
            self._checked_file(take["project_id"], take["relative_path"], take["sha256"])
            old = conn.execute("SELECT * FROM video_selections WHERE shot_id=? AND stage=?", (take["shot_id"], take["stage"])).fetchone()
            if old and old["take_id"] == take_id:
                return dict(old)
            revision = old["revision"] + 1 if old else 1
            conn.execute("INSERT INTO video_selections(shot_id,stage,take_id,revision,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(shot_id,stage) DO UPDATE SET take_id=excluded.take_id,revision=excluded.revision,updated_at=excluded.updated_at",
                         (take["shot_id"], take["stage"], take_id, revision, now()))
            if take["stage"] == "FINAL":
                self._stale_picture(conn, take["shot_id"])
            else:
                conn.execute("UPDATE video_takes SET validity='STALE' WHERE shot_id=? AND stage='FINAL' AND validity='CURRENT'", (take["shot_id"],))
                self._stale_picture(conn, take["shot_id"])
            self.studio.audit(conn, take["project_id"], "video.take_selected", take_id, {"stage": take["stage"], "selection_revision": revision})
            return dict(conn.execute("SELECT * FROM video_selections WHERE shot_id=? AND stage=?", (take["shot_id"], take["stage"])).fetchone())

    @staticmethod
    def _stale_picture(conn, shot_id: str) -> None:
        conn.execute("UPDATE picture_locks SET validity='STALE' WHERE shot_id=? AND validity='CURRENT'", (shot_id,))
        conn.execute("UPDATE sound_stems SET validity='STALE' WHERE shot_id=? AND validity='CURRENT'", (shot_id,))
        conn.execute("UPDATE delivery_packages SET state='STALE' WHERE episode_id=(SELECT episode_id FROM shots WHERE id=?) AND state!='STALE'", (shot_id,))

    def picture_preflight(self, shot_id: str) -> dict:
        shot = self.studio.shot(shot_id)
        blockers = []
        with self.studio.db.connect() as conn:
            route = self._approval(conn, self._shot(conn, shot_id))
        if not route:
            blockers.append("C1 route chưa duyệt đúng revision")
        with self.studio.db.connect() as conn:
            selected = conn.execute("SELECT t.* FROM video_selections s JOIN video_takes t ON t.id=s.take_id WHERE s.shot_id=? AND s.stage='FINAL'", (shot_id,)).fetchone()
            if not selected or selected["review"] != "APPROVED" or selected["validity"] != "CURRENT" or selected["shot_revision"] != shot["current_revision"] or selected["route"] != route:
                blockers.append("Thiếu selected final take đã duyệt, đúng route/revision")
            elif not self._file_current(shot["project_id"], selected):
                blockers.append("Final take thiếu file hoặc checksum thay đổi")
            elif route == "UNREAL" and selected["provenance"] != "UE_MRQ_VERIFIED":
                blockers.append("UE final take chưa có MRQ receipt kiểm chứng")
            elif route in ("SEEDANCE", "HYBRID") and selected["provenance"] != "SEEDANCE_LIVE_VERIFIED":
                blockers.append("AI final take chưa có live provider receipt kiểm chứng")
            timing = conn.execute("SELECT * FROM timing_revisions WHERE shot_id=? ORDER BY version DESC LIMIT 1", (shot_id,)).fetchone()
            if any(p.get("dialogue_id") or p.get("voice_profile_id") for p in shot["manifest"]["performances"]):
                if not timing or timing["review"] != "APPROVED" or timing["validity"] != "CURRENT" or timing["shot_revision"] != shot["current_revision"]:
                    blockers.append("C2 voice/timing chưa current và được duyệt")
                elif not self._timing_current(conn, timing):
                    blockers.append("Voice canon/selection/checksum không còn khớp timing revision")
            state = conn.execute("SELECT * FROM subsystem_states WHERE shot_id=? AND subsystem='render'", (shot_id,)).fetchone()
            if state and (state["observed_fingerprint"] and state["observed_fingerprint"] != state["synced_fingerprint"]):
                blockers.append("Render có conflict chỉnh ngoài app")
        if route in ("UNREAL", "HYBRID"):
            blockers.extend(self.studio_p4_picture_blockers(shot_id))
        return {"allowed": not blockers, "blockers": blockers, "route": route,
                "selected_final_take_id": selected["id"] if selected else None,
                "timing_version": timing["version"] if timing else None, "shot_revision": shot["current_revision"]}

    def studio_p4_picture_blockers(self, shot_id: str) -> list[str]:
        if self.cache_render is None:
            return ["P4 final-render guard chưa được nối với dịch vụ Picture Lock"]
        return self.cache_render.picture_lock_preflight(shot_id)["blockers"]

    def _timing_current(self, conn, timing) -> bool:
        for placement in json.loads(timing["placements_json"]):
            take = conn.execute("SELECT t.*,s.revision AS selection_revision FROM voice_selections s JOIN voice_takes t ON t.id=s.take_id WHERE s.shot_id=? AND t.id=?", (timing["shot_id"], placement["voice_take_id"])).fetchone()
            if not take or take["lock_state"] != "LOCKED" or take["review"] != "APPROVED" or take["validity"] != "CURRENT" or take["selection_revision"] != placement["selection_revision"] or take["version"] != placement["voice_version"] or take["wav_sha256"] != placement["wav_sha256"]:
                return False
            try:
                self._checked_file(take["project_id"], take["relative_path"], take["wav_sha256"])
            except DomainError:
                return False
        return True

    def lock_picture(self, shot_id: str, expected_shot_revision: int, expected_take_id: str,
                     edit_in: int, edit_out_exclusive: int) -> dict:
        check = self.picture_preflight(shot_id)
        if not check["allowed"]:
            raise DomainError("; ".join(check["blockers"]), 409)
        shot = self.studio.shot(shot_id)
        timeline = shot["manifest"]["timeline"]
        if expected_shot_revision != shot["current_revision"] or expected_take_id != check["selected_final_take_id"] or type(edit_in) is not int or type(edit_out_exclusive) is not int or (edit_in, edit_out_exclusive) != (timeline["in"], timeline["out_exclusive"]):
            raise DomainError("Edit timing phải khớp exact shot/frame range và selected final take.", 409)
        with self.studio.db.transaction() as conn:
            row = self._shot(conn, shot_id)
            selected = conn.execute("SELECT take_id FROM video_selections WHERE shot_id=? AND stage='FINAL'", (shot_id,)).fetchone()
            if row["current_revision"] != expected_shot_revision or not selected or selected[0] != expected_take_id:
                raise DomainError("Picture selection/revision đổi trước khóa.", 409)
            fresh = self.picture_preflight(shot_id)
            if not fresh["allowed"] or fresh["timing_version"] != check["timing_version"] or fresh["route"] != check["route"]:
                raise DomainError("Gate/timing/route đổi trước Picture Lock.", 409)
            input_hash = digest({"shot_revision": expected_shot_revision, "take_id": expected_take_id,
                                 "edit": [edit_in, edit_out_exclusive], "timing_version": check["timing_version"]})
            old = conn.execute("SELECT * FROM picture_locks WHERE shot_id=? AND input_hash=? AND validity='CURRENT'", (shot_id, input_hash)).fetchone()
            if old:
                return dict(old)
            self._stale_picture(conn, shot_id)
            version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM picture_locks WHERE shot_id=?", (shot_id,)).fetchone()[0]
            item = {"id": uid(), "project_id": shot["project_id"], "shot_id": shot_id, "version": version,
                    "shot_revision": expected_shot_revision, "final_take_id": expected_take_id, "edit_in": edit_in,
                    "edit_out_exclusive": edit_out_exclusive, "timing_version": check["timing_version"],
                    "input_hash": input_hash, "validity": "CURRENT", "created_at": now()}
            conn.execute("INSERT INTO picture_locks VALUES(:id,:project_id,:shot_id,:version,:shot_revision,:final_take_id,:edit_in,:edit_out_exclusive,:timing_version,:input_hash,:validity,:created_at)", item)
            conn.execute("INSERT INTO approvals(id,project_id,scope,scope_id,exact_version,decision_json,actor,created_at) VALUES(?,?,'picture_lock',?,?,?,'local_user',?)",
                         (uid(), shot["project_id"], shot_id, version, canonical({"take_id": expected_take_id, "input_hash": input_hash}), now()))
            self.studio.audit(conn, shot["project_id"], "picture.locked", item["id"], {"version": version, "input_hash": input_hash})
            return item

    def picture_locks(self, shot_id: str) -> list[dict]:
        self.studio.shot(shot_id)
        with self.studio.db.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM picture_locks WHERE shot_id=? ORDER BY version DESC", (shot_id,))]
