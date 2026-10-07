"""P4 metadata, filesystem verification and fail-closed render planning.

No Unreal cache bake/playback or MRQ write is claimed by this module.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import gpu_queue, workflow
from .domain import DomainError, canonical, digest, safe_relative
from .p4_domain import CACHE_KINDS, cache_blockers, file_sha256, fingerprint, frame_inventory, required_range, validate_source
from .service import Studio, now, require, uid


class CacheRenderService:
    def __init__(self, studio: Studio):
        self.studio = studio
        with studio.db.transaction() as conn:
            conn.execute("UPDATE gpu_heavy_leases SET state='RECONCILING' WHERE state='ACTIVE'")

    def _root(self, project_id: str) -> Path:
        return Path(self.studio.project(project_id)["root"]).resolve()

    @staticmethod
    def _stale_media_downstream(conn, shot_id: str) -> None:
        conn.execute("UPDATE video_takes SET validity='STALE' WHERE shot_id=? AND validity='CURRENT'", (shot_id,))
        conn.execute("UPDATE picture_locks SET validity='STALE' WHERE shot_id=? AND validity='CURRENT'", (shot_id,))
        conn.execute("UPDATE sound_stems SET validity='STALE' WHERE shot_id=? AND validity='CURRENT'", (shot_id,))
        conn.execute("UPDATE delivery_packages SET state='STALE' WHERE episode_id=(SELECT episode_id FROM shots WHERE id=?) AND state!='STALE'", (shot_id,))

    def _file(self, project_id: str, relative_path: str) -> tuple[str, Path]:
        root = self._root(project_id)
        relative = safe_relative(root, relative_path)
        return relative, (root / relative).resolve()

    def _source_fingerprint(self, conn, shot: dict, subsystem: str, source: dict) -> str:
        validate_source(source, subsystem)
        revision = require(conn.execute("SELECT manifest_json FROM shot_revisions WHERE shot_id=? AND revision=?",
                                        (shot["id"], shot["current_revision"])).fetchone(), "shot revision")
        manifest = json.loads(revision["manifest_json"])
        if source["view_lod_dependency"]:
            if source["camera_fingerprint"] != digest(manifest.get("camera_intent")):
                raise DomainError("Camera/view-LOD fingerprint đã đổi; cache cần bake lại.", 409)
        body = require(conn.execute("SELECT * FROM artifact_versions WHERE id=? AND shot_id=? AND role='body'",
                                    (source["body_artifact_id"], shot["id"])).fetchone(), "body artifact")
        if body["validity"] != "CURRENT":
            raise DomainError("Body artifact đã STALE; cần lắp dựng lại trước cache.", 409)
        asset = require(conn.execute("SELECT * FROM asset_versions WHERE id=? AND project_id=?",
                                     (source["asset_version_id"], shot["project_id"])).fetchone(), "cache asset")
        kinds = {"hair": "groom", "cloth": "costume", "vfx": "niagara_effect"}
        if asset["kind"] != kinds[subsystem]:
            raise DomainError("Cache asset không đúng loại subsystem.", 409)
        latest = conn.execute("SELECT MAX(version) FROM asset_versions WHERE project_id=? AND registry_id=?",
                              (shot["project_id"], asset["registry_id"])).fetchone()[0]
        if latest != asset["version"]:
            raise DomainError("Cache asset version đã cũ.", 409)
        return fingerprint(subsystem, source, asset["checksum"], body["input_hash"],
                           required_range(manifest["timeline"], source))

    def _require_upstream_lock(self, conn, shot: dict) -> None:
        # Workflow V4.5: dialogue timing is approved before any animation is baked or cached.
        workflow.require_production_ready(self.studio, conn, shot["project_id"], shot["episode_id"])
        for subsystem in ("body", "environment"):
            state = require(conn.execute("SELECT * FROM subsystem_states WHERE shot_id=? AND subsystem=?", (shot["id"], subsystem)).fetchone(), "subsystem")
            if state["lock_state"] != "LOCKED" or state["validity"] != "CURRENT" or state["review"] != "APPROVED":
                raise DomainError(f"Cần khóa và duyệt {subsystem} hiện hành trước simulation.", 409)
            if state["observed_fingerprint"] and state["observed_fingerprint"] != state["synced_fingerprint"]:
                raise DomainError(f"{subsystem}: chỉnh ngoài app chưa adopt/sync.", 409)
            approval = conn.execute("SELECT 1 FROM approvals WHERE project_id=? AND scope=? AND scope_id=? AND exact_version=?",
                                    (shot["project_id"], f"subsystem:{subsystem}", shot["id"], state["state_revision"] - 1)).fetchone()
            if not approval:
                raise DomainError(f"{subsystem}: thiếu approval exact state version.", 409)

    def declare_present(self, shot_id: str, subsystem: str, expected_shot_revision: int) -> dict:
        if subsystem not in CACHE_KINDS:
            raise DomainError("Loại simulation không hợp lệ.")
        with self.studio.db.transaction() as conn:
            shot = require(conn.execute("SELECT * FROM shots WHERE id=?", (shot_id,)).fetchone(), "shot")
            if shot["current_revision"] != expected_shot_revision:
                raise DomainError("Shot revision đã thay đổi.", 409)
            self._require_upstream_lock(conn, shot)
            conn.execute("INSERT INTO simulation_applicability(shot_id,subsystem,status,reason,observation_receipt_json,shot_revision,updated_at) VALUES(?,?,'PRESENT',NULL,NULL,?,?) ON CONFLICT(shot_id,subsystem) DO UPDATE SET status='PRESENT',reason=NULL,observation_receipt_json=NULL,shot_revision=excluded.shot_revision,updated_at=excluded.updated_at",
                         (shot_id, subsystem, expected_shot_revision, now()))
            self.studio.audit(conn, shot["project_id"], "cache.present_declared", shot_id, {"subsystem": subsystem})
        return {"shot_id": shot_id, "subsystem": subsystem, "status": "PRESENT"}

    def mark_not_applicable(self, _shot_id: str, _subsystem: str, _reason: str) -> dict:
        raise DomainError("NOT_APPLICABLE cần receipt quan sát subsystem vắng mặt từ UE agent đã kiểm chứng; P0 cache playback còn BLOCKED.", 409)

    def configure_source(self, shot_id: str, subsystem: str, expected_shot_revision: int, source: dict) -> dict:
        if subsystem not in CACHE_KINDS:
            raise DomainError("Loại simulation không hợp lệ.")
        with self.studio.db.transaction() as conn:
            shot = require(conn.execute("SELECT * FROM shots WHERE id=?", (shot_id,)).fetchone(), "shot")
            if shot["current_revision"] != expected_shot_revision:
                raise DomainError("Shot revision đã thay đổi.", 409)
            self._require_upstream_lock(conn, shot)
            state = require(conn.execute("SELECT * FROM subsystem_states WHERE shot_id=? AND subsystem=?", (shot_id, subsystem)).fetchone(), "subsystem")
            if state["mode"] != "AUTO" or state["lock_state"] != "UNLOCKED" or (state["observed_fingerprint"] and state["observed_fingerprint"] != state["synced_fingerprint"]):
                raise DomainError("Simulation MANUAL/LOCKED hoặc có chỉnh ngoài app.", 409)
            new_hash = self._source_fingerprint(conn, shot, subsystem, source)
            old = conn.execute("SELECT input_fingerprint FROM simulation_applicability WHERE shot_id=? AND subsystem=?", (shot_id, subsystem)).fetchone()
            conn.execute("INSERT INTO simulation_applicability(shot_id,subsystem,status,reason,observation_receipt_json,source_json,input_fingerprint,shot_revision,updated_at) VALUES(?,?,'PRESENT',NULL,NULL,?,?,?,?) ON CONFLICT(shot_id,subsystem) DO UPDATE SET status='PRESENT',reason=NULL,observation_receipt_json=NULL,source_json=excluded.source_json,input_fingerprint=excluded.input_fingerprint,shot_revision=excluded.shot_revision,updated_at=excluded.updated_at",
                         (shot_id, subsystem, canonical(source), new_hash, expected_shot_revision, now()))
            if old and old["input_fingerprint"] != new_hash:
                conn.execute("UPDATE simulation_cache_versions SET validity='STALE' WHERE shot_id=? AND subsystem=? AND validity='CURRENT'", (shot_id, subsystem))
                conn.execute("UPDATE subsystem_states SET validity='STALE',review='NEEDS_REVIEW',state_revision=state_revision+1 WHERE shot_id=? AND subsystem=?", (shot_id, subsystem))
                conn.execute("UPDATE subsystem_states SET validity='STALE',review='NEEDS_REVIEW',state_revision=state_revision+1 WHERE shot_id=? AND subsystem='render'", (shot_id,))
                conn.execute("UPDATE artifact_versions SET validity='STALE' WHERE shot_id=? AND role='render'", (shot_id,))
                self._stale_media_downstream(conn, shot_id)
            self.studio.audit(conn, shot["project_id"], "cache.source_configured", shot_id,
                              {"subsystem": subsystem, "input_fingerprint": new_hash, "cache_stale": bool(old and old["input_fingerprint"] != new_hash)})
            return {"shot_id": shot_id, "subsystem": subsystem, "input_fingerprint": new_hash,
                    "cache_stale": bool(old and old["input_fingerprint"] != new_hash)}

    def preview_source(self, shot_id: str, subsystem: str, expected_shot_revision: int, source: dict) -> dict:
        if subsystem not in CACHE_KINDS:
            raise DomainError("Loại simulation không hợp lệ.")
        with self.studio.db.connect() as conn:
            shot = require(conn.execute("SELECT * FROM shots WHERE id=?", (shot_id,)).fetchone(), "shot")
            if shot["current_revision"] != expected_shot_revision:
                raise DomainError("Shot revision đã thay đổi.", 409)
            self._require_upstream_lock(conn, shot)
            new_hash = self._source_fingerprint(conn, shot, subsystem, source)
            old = conn.execute("SELECT input_fingerprint FROM simulation_applicability WHERE shot_id=? AND subsystem=?", (shot_id, subsystem)).fetchone()
            changes = bool(old and old["input_fingerprint"] != new_hash)
            caches = [dict(row) for row in conn.execute("SELECT id,version,output_sha256,relative_path FROM simulation_cache_versions WHERE shot_id=? AND subsystem=? AND validity='CURRENT'", (shot_id, subsystem))] if changes else []
            return {"shot_id": shot_id, "subsystem": subsystem, "new_input_fingerprint": new_hash,
                    "input_changed": changes, "stale_cache_versions": caches,
                    "affected_subsystems": [subsystem, "render"] if changes else [],
                    "old_files_preserved": True}

    def register_file(self, shot_id: str, subsystem: str, expected_shot_revision: int,
                      source: dict, range_in: int, range_out_exclusive: int,
                      relative_path: str, expected_sha256: str) -> dict:
        if subsystem not in CACHE_KINDS or not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
            raise DomainError("Cache kind hoặc SHA-256 không hợp lệ.")
        shot = self.studio.shot(shot_id)
        relative, path = self._file(shot["project_id"], relative_path)
        if not path.is_file() or file_sha256(path) != expected_sha256.lower():
            raise DomainError("File cache thiếu hoặc checksum không khớp.", 409)
        if type(range_in) is not int or type(range_out_exclusive) is not int or range_out_exclusive <= range_in:
            raise DomainError("Frame range cache phải là [in,out) hợp lệ.")
        validate_source(source, subsystem)
        need_in, need_out = required_range(shot["manifest"]["timeline"], source)
        if range_in > need_in or range_out_exclusive < need_out:
            raise DomainError(f"Cache cần phủ [{need_in},{need_out}) gồm warm-up và handles.", 409)
        with self.studio.db.transaction() as conn:
            current = require(conn.execute("SELECT * FROM shots WHERE id=?", (shot_id,)).fetchone(), "shot")
            if current["current_revision"] != expected_shot_revision:
                raise DomainError("Shot revision thay đổi trước khi đăng ký cache.", 409)
            self._require_upstream_lock(conn, current)
            state = require(conn.execute("SELECT * FROM subsystem_states WHERE shot_id=? AND subsystem=?", (shot_id, subsystem)).fetchone(), "subsystem")
            if state["mode"] != "AUTO" or state["lock_state"] != "UNLOCKED" or (state["observed_fingerprint"] and state["observed_fingerprint"] != state["synced_fingerprint"]):
                raise DomainError("Cache subsystem MANUAL/LOCKED hoặc có chỉnh ngoài app.", 409)
            declared = conn.execute("SELECT * FROM simulation_applicability WHERE shot_id=? AND subsystem=?", (shot_id, subsystem)).fetchone()
            if declared is None or declared["status"] != "PRESENT" or not declared["source_json"]:
                raise DomainError("Cần cấu hình nguồn simulation trong app trước khi đăng ký cache.", 409)
            input_fingerprint = self._source_fingerprint(conn, current, subsystem, source)
            if declared["input_fingerprint"] != input_fingerprint or declared["source_json"] != canonical(source):
                raise DomainError("Nguồn cache khác cấu hình hiện hành; xem impact rồi lưu nguồn mới.", 409)
            if not path.is_file() or file_sha256(path) != expected_sha256.lower():
                raise DomainError("File cache thay đổi trước khi đăng ký; cần chọn lại.", 409)
            previous = conn.execute("SELECT * FROM simulation_cache_versions WHERE shot_id=? AND subsystem=? AND input_fingerprint=? AND output_sha256=? AND relative_path=? AND range_in=? AND range_out_exclusive=? AND validity='CURRENT' ORDER BY version DESC LIMIT 1",
                                    (shot_id, subsystem, input_fingerprint, expected_sha256.lower(), relative, range_in, range_out_exclusive)).fetchone()
            if previous:
                return dict(previous)
            version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM simulation_cache_versions WHERE shot_id=? AND subsystem=?", (shot_id, subsystem)).fetchone()[0]
            item = {"id": uid(), "project_id": shot["project_id"], "shot_id": shot_id, "subsystem": subsystem,
                    "version": version, "shot_revision": expected_shot_revision, "source_json": canonical(source),
                    "input_fingerprint": input_fingerprint, "range_in": range_in, "range_out_exclusive": range_out_exclusive,
                    "relative_path": relative, "output_sha256": expected_sha256.lower(), "playback_status": "UNVERIFIED",
                    "playback_receipt_json": None, "review": "NEEDS_REVIEW", "validity": "CURRENT",
                    "provenance": "LOCAL_FILE_REGISTRATION_UNVERIFIED", "created_at": now()}
            conn.execute("UPDATE simulation_cache_versions SET validity='STALE' WHERE shot_id=? AND subsystem=? AND validity='CURRENT'", (shot_id, subsystem))
            conn.execute("INSERT INTO simulation_cache_versions(id,project_id,shot_id,subsystem,version,shot_revision,source_json,input_fingerprint,range_in,range_out_exclusive,relative_path,output_sha256,playback_status,playback_receipt_json,review,validity,provenance,created_at) VALUES(:id,:project_id,:shot_id,:subsystem,:version,:shot_revision,:source_json,:input_fingerprint,:range_in,:range_out_exclusive,:relative_path,:output_sha256,:playback_status,:playback_receipt_json,:review,:validity,:provenance,:created_at)", item)
            conn.execute("UPDATE subsystem_states SET validity='STALE',review='NEEDS_REVIEW',state_revision=state_revision+1 WHERE shot_id=? AND subsystem=?", (shot_id, subsystem))
            self._stale_media_downstream(conn, shot_id)
            self.studio.audit(conn, shot["project_id"], "cache.file_registered", item["id"], {"subsystem": subsystem, "version": version, "fingerprint": input_fingerprint})
            return item

    def caches(self, shot_id: str) -> list[dict]:
        self.studio.shot(shot_id)
        with self.studio.db.connect() as conn:
            rows = conn.execute("SELECT * FROM simulation_cache_versions WHERE shot_id=? ORDER BY subsystem,version DESC", (shot_id,))
            return [dict(row, source=json.loads(row["source_json"])) for row in rows]

    def source_hints(self, shot_id: str) -> dict:
        shot = self.studio.shot(shot_id)
        with self.studio.db.connect() as conn:
            configured = {row["subsystem"]: {"source": json.loads(row["source_json"]), "input_fingerprint": row["input_fingerprint"]}
                          for row in conn.execute("SELECT subsystem,source_json,input_fingerprint FROM simulation_applicability WHERE shot_id=? AND status='PRESENT' AND source_json IS NOT NULL", (shot_id,))}
        return {"camera_fingerprint": digest(shot["manifest"].get("camera_intent")),
                "shot_revision": shot["current_revision"], "shot_range": shot["manifest"]["timeline"],
                "configured_sources": configured}

    def review_cache(self, cache_id: str, expected_version: int) -> dict:
        with self.studio.db.transaction() as conn:
            cache = require(conn.execute("SELECT * FROM simulation_cache_versions WHERE id=?", (cache_id,)).fetchone(), "cache")
            if cache["version"] != expected_version or cache["validity"] != "CURRENT" or cache["review"] != "NEEDS_REVIEW":
                raise DomainError("Cache version không còn chờ duyệt.", 409)
            shot = require(conn.execute("SELECT * FROM shots WHERE id=?", (cache["shot_id"],)).fetchone(), "shot")
            self._require_upstream_lock(conn, shot)
            declared = conn.execute("SELECT input_fingerprint FROM simulation_applicability WHERE shot_id=? AND subsystem=?", (cache["shot_id"], cache["subsystem"])).fetchone()
            source = json.loads(cache["source_json"])
            if not declared or declared["input_fingerprint"] != cache["input_fingerprint"] or self._source_fingerprint(conn, shot, cache["subsystem"], source) != cache["input_fingerprint"]:
                raise DomainError("Nguồn cache đã đổi trước khi duyệt.", 409)
            manifest = json.loads(conn.execute("SELECT manifest_json FROM shot_revisions WHERE shot_id=? AND revision=?", (cache["shot_id"], shot["current_revision"])).fetchone()[0])
            required_in, required_out = required_range(manifest["timeline"], source)
            if cache["range_in"] > required_in or cache["range_out_exclusive"] < required_out:
                raise DomainError("Cache không còn phủ đủ warm-up/handles/frame range.", 409)
            relative, path = self._file(cache["project_id"], cache["relative_path"])
            if not path.is_file() or file_sha256(path) != cache["output_sha256"]:
                raise DomainError("Cache thiếu hoặc checksum thay đổi trước khi duyệt.", 409)
            conn.execute("UPDATE simulation_cache_versions SET review='APPROVED' WHERE id=?", (cache_id,))
            conn.execute("INSERT INTO approvals(id,project_id,scope,scope_id,exact_version,decision_json,actor,created_at) VALUES(?,?,'simulation_cache',?,?,?,'local_user',?)",
                         (uid(), cache["project_id"], cache_id, expected_version, canonical({"file_sha256": cache["output_sha256"], "playback": "UNVERIFIED"}), now()))
            self.studio.audit(conn, cache["project_id"], "cache.file_reviewed", cache_id, {"version": expected_version})
            return dict(require(conn.execute("SELECT * FROM simulation_cache_versions WHERE id=?", (cache_id,)).fetchone()))

    def cache_status(self, shot_id: str) -> dict:
        shot = self.studio.shot(shot_id)
        with self.studio.db.connect() as conn:
            details = {}
            for subsystem in CACHE_KINDS:
                declaration = conn.execute("SELECT * FROM simulation_applicability WHERE shot_id=? AND subsystem=?", (shot_id, subsystem)).fetchone()
                latest = conn.execute("SELECT * FROM simulation_cache_versions WHERE shot_id=? AND subsystem=? ORDER BY version DESC LIMIT 1", (shot_id, subsystem)).fetchone()
                if declaration and declaration["status"] == "NOT_APPLICABLE" and declaration["shot_revision"] == shot["current_revision"] and declaration["observation_receipt_json"]:
                    details[subsystem] = {"status": "NOT_APPLICABLE", "blockers": []}
                    continue
                if not declaration or declaration["status"] != "PRESENT":
                    details[subsystem] = {"status": "UNKNOWN", "blockers": ["chưa có quan sát UE xác nhận có/vắng subsystem"]}
                    continue
                if not latest:
                    details[subsystem] = {"status": "PRESENT", "blockers": ["thiếu bản cache"]}
                    continue
                cache = dict(latest)
                source = json.loads(cache["source_json"])
                try:
                    expected = self._source_fingerprint(conn, shot, subsystem, source) if declaration["input_fingerprint"] == cache["input_fingerprint"] and declaration["source_json"] == cache["source_json"] else None
                except DomainError:
                    expected = None
                need_in, need_out = required_range(shot["manifest"]["timeline"], source)
                _, path = self._file(shot["project_id"], cache["relative_path"])
                details[subsystem] = {"status": "PRESENT", "cache_id": cache["id"], "version": cache["version"],
                                      "provenance": cache["provenance"], "input_fingerprint": cache["input_fingerprint"],
                                      "required_range": [need_in, need_out],
                                      "blockers": cache_blockers(cache, expected_fingerprint=expected,
                                                                 required_in=need_in, required_out=need_out, path=path)}
            return details

    def final_preflight(self, shot_id: str) -> dict:
        shot = self.studio.shot(shot_id)
        blockers = []
        with self.studio.db.connect() as conn:
            approval = shot["approval"]
            if not approval or approval["decision"]["approved_route"] != "UNREAL":
                blockers.append("plan/current route UNREAL chưa được duyệt")
            if not shot["manifest"].get("render_profile_id"):
                blockers.append("thiếu render preset/version trong manifest")
            else:
                pinned = shot["resolved_registry"].get(shot["manifest"]["render_profile_id"])
                asset = conn.execute("SELECT * FROM asset_versions WHERE id=?", (pinned["asset_version_id"],)).fetchone() if pinned else None
                meta = json.loads(asset["metadata_json"]) if asset else {}
                if not asset or not meta.get("color_profile") or meta.get("fps") != shot["manifest"]["fps"]:
                    blockers.append("render preset thiếu color/FPS hoặc không khớp shot")
            for subsystem in ("body", "face", "audio", "environment", "camera"):
                state = next(s for s in shot["states"] if s["subsystem"] == subsystem)
                if state["validity"] != "CURRENT" or state["review"] != "APPROVED":
                    blockers.append(f"{subsystem}: chưa current/approved")
                if subsystem in ("body", "environment") and state["lock_state"] != "LOCKED":
                    blockers.append(f"{subsystem}: chưa khóa trước simulation/final")
                if state["observed_fingerprint"] and state["observed_fingerprint"] != state["synced_fingerprint"]:
                    blockers.append(f"{subsystem}: conflict chỉnh ngoài app")
                exact = conn.execute("SELECT 1 FROM approvals WHERE project_id=? AND scope=? AND scope_id=? AND exact_version=?",
                                     (shot["project_id"], f"subsystem:{subsystem}", shot_id, state["state_revision"] - 1)).fetchone()
                if not exact:
                    blockers.append(f"{subsystem}: thiếu approval exact state version")
            if any(p.get("voice_profile_id") for p in shot["manifest"]["performances"]):
                timing = conn.execute("SELECT * FROM timing_revisions WHERE shot_id=? ORDER BY version DESC LIMIT 1", (shot_id,)).fetchone()
                if not timing or timing["review"] != "APPROVED" or timing["validity"] != "CURRENT" or timing["shot_revision"] != shot["current_revision"]:
                    blockers.append("voice/timing revision hiện hành chưa khóa và duyệt")
                else:
                    for place in json.loads(timing["placements_json"]):
                        selection = conn.execute("SELECT v.* FROM voice_selections s JOIN voice_takes v ON v.id=s.take_id WHERE s.shot_id=? AND s.performance_id=?", (shot_id, place["performance_id"])).fetchone()
                        if not selection or selection["id"] != place["voice_take_id"] or selection["version"] != place["voice_version"] or selection["lock_state"] != "LOCKED" or selection["validity"] != "CURRENT":
                            blockers.append(f"{place['performance_id']}: voice take không còn đúng bản khóa")
        caches = self.cache_status(shot_id)
        for subsystem, detail in caches.items():
            blockers.extend(f"{subsystem}: {reason}" for reason in detail["blockers"])
        blockers.append("P0 chưa chứng minh cache playback/MRQ và P2/P3 chưa có UE performance receipt; pre-write UE bị chặn")
        return {"shot_id": shot_id, "shot_revision": shot["current_revision"], "allowed": False,
                "blockers": blockers, "caches": caches, "capability": "BLOCKED_P0_P2_P3_UNVERIFIED",
                "prewrite_contract": "Recompute this guard in UE immediately before any final MRQ write."}

    def request_final(self, shot_id: str) -> dict:
        result = self.final_preflight(shot_id)
        raise DomainError("; ".join(result["blockers"]), 409)

    def inventory_render(self, shot_id: str, kind: str, expected_shot_revision: int,
                         relative_folder: str, color_profile: str) -> dict:
        if kind not in ("PROXY", "FINAL") or not isinstance(color_profile, str) or not color_profile:
            raise DomainError("Render kind/color profile không hợp lệ.")
        shot = self.studio.shot(shot_id)
        relative, folder = self._file(shot["project_id"], relative_folder)
        if shot["current_revision"] != expected_shot_revision or not folder.is_dir():
            raise DomainError("Shot revision đã đổi hoặc folder render không tồn tại.", 409)
        manifest = shot["manifest"]
        start, end = manifest["timeline"]["in"], manifest["timeline"]["out_exclusive"]
        inventory = frame_inventory(folder, start, end)
        with self.studio.db.transaction() as conn:
            current = conn.execute("SELECT current_revision FROM shots WHERE id=?", (shot_id,)).fetchone()
            if current[0] != expected_shot_revision:
                raise DomainError("Shot revision đổi trong lúc kiểm kê.", 409)
            version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM render_inventories WHERE shot_id=? AND kind=?", (shot_id, kind)).fetchone()[0]
            item = {"id": uid(), "project_id": shot["project_id"], "shot_id": shot_id, "kind": kind,
                    "version": version, "shot_revision": expected_shot_revision, "input_hash": shot["input_hash"],
                    "preset_version_id": shot["resolved_registry"].get(manifest.get("render_profile_id"), {}).get("asset_version_id"),
                    "fps_num": manifest["fps"]["num"], "fps_den": manifest["fps"]["den"],
                    "color_profile": color_profile, "relative_folder": relative, "expected_in": start,
                    "expected_out_exclusive": end, "frame_inventory_json": canonical(inventory),
                    "state": inventory["state"], "provenance": "FILESYSTEM_INVENTORY_UNVERIFIED_MRQ", "created_at": now()}
            conn.execute("INSERT INTO render_inventories(id,project_id,shot_id,kind,version,shot_revision,input_hash,preset_version_id,fps_num,fps_den,color_profile,relative_folder,expected_in,expected_out_exclusive,frame_inventory_json,state,provenance,created_at) VALUES(:id,:project_id,:shot_id,:kind,:version,:shot_revision,:input_hash,:preset_version_id,:fps_num,:fps_den,:color_profile,:relative_folder,:expected_in,:expected_out_exclusive,:frame_inventory_json,:state,:provenance,:created_at)", item)
            self.studio.audit(conn, shot["project_id"], "render.inventory", item["id"], {"kind": kind, "version": version, "state": item["state"], "found": len(inventory["frames"])})
            return dict(item, inventory=inventory)

    def render_inventories(self, shot_id: str) -> list[dict]:
        shot = self.studio.shot(shot_id)
        with self.studio.db.connect() as conn:
            return [dict(row, inventory=json.loads(row["frame_inventory_json"]),
                         validity="CURRENT" if row["shot_revision"] == shot["current_revision"] and row["input_hash"] == shot["input_hash"] else "STALE")
                    for row in conn.execute("SELECT * FROM render_inventories WHERE shot_id=? ORDER BY created_at DESC", (shot_id,))]

    def picture_lock_preflight(self, shot_id: str) -> dict:
        final = self.final_preflight(shot_id)
        inventories = self.render_inventories(shot_id)
        blockers = list(final["blockers"])
        shot = self.studio.shot(shot_id)
        final_files = [r for r in inventories if r["kind"] == "FINAL" and r["state"] == "FILES_COMPLETE_UNVERIFIED"
                       and r["shot_revision"] == shot["current_revision"] and r["input_hash"] == shot["input_hash"]]
        intact_final = []
        for record in final_files:
            _, folder = self._file(shot["project_id"], record["relative_folder"])
            observed = frame_inventory(folder, record["expected_in"], record["expected_out_exclusive"])
            if observed["state"] == "FILES_COMPLETE_UNVERIFIED" and observed["frames"] == record["inventory"]["frames"]:
                intact_final.append(record)
        if final_files and not intact_final:
            blockers.append("file FINAL thiếu hoặc checksum đổi sau lần kiểm kê")
        final_files = intact_final
        if not final_files:
            blockers.append("thiếu inventory FINAL đủ frame; PROXY không thay thế Picture Lock")
        elif shot["manifest"].get("render_profile_id"):
            pinned = shot["resolved_registry"].get(shot["manifest"]["render_profile_id"])
            with self.studio.db.connect() as conn:
                asset = conn.execute("SELECT metadata_json FROM asset_versions WHERE id=?", (pinned["asset_version_id"],)).fetchone() if pinned else None
            expected_color = json.loads(asset[0]).get("color_profile") if asset else None
            if not expected_color or not any(r["color_profile"] == expected_color for r in final_files):
                blockers.append("color profile inventory không khớp render preset đã pin")
        blockers.append("chưa có selected final UE take được người dùng duyệt")
        return {"allowed": False, "blockers": blockers, "inventories": inventories}

    def acquire_heavy_lease(self, operation_id: str, owner_kind: str) -> dict:
        if not operation_id or owner_kind not in gpu_queue.OWNER_KINDS:
            raise DomainError("Operation/resource lease không hợp lệ.")
        with self.studio.db.transaction() as conn:
            row = conn.execute("SELECT * FROM gpu_heavy_leases WHERE resource='GPU_HEAVY'").fetchone()
            if row:
                if row["operation_id"] == operation_id:
                    return dict(row)
                raise DomainError("GPU_HEAVY đã có công việc khác; cần đối soát lease trước khi chạy.", 409)
            conn.execute("INSERT INTO gpu_heavy_leases(resource,operation_id,owner_kind,heartbeat_at,state) VALUES('GPU_HEAVY',?,?,?,'ACTIVE')", (operation_id, owner_kind, now()))
            return dict(conn.execute("SELECT * FROM gpu_heavy_leases WHERE resource='GPU_HEAVY'").fetchone())

    def heartbeat_heavy_lease(self, operation_id: str) -> dict:
        with self.studio.db.transaction() as conn:
            row = require(conn.execute("SELECT * FROM gpu_heavy_leases WHERE resource='GPU_HEAVY' AND operation_id=?", (operation_id,)).fetchone(), "GPU lease")
            if row["state"] != "ACTIVE":
                raise DomainError("Lease đang RECONCILING; không được heartbeat như job đang chạy.", 409)
            conn.execute("UPDATE gpu_heavy_leases SET heartbeat_at=? WHERE operation_id=?", (now(), operation_id))
            return dict(conn.execute("SELECT * FROM gpu_heavy_leases WHERE resource='GPU_HEAVY'").fetchone())

    def heavy_lease(self) -> dict | None:
        with self.studio.db.connect() as conn:
            row = conn.execute("SELECT * FROM gpu_heavy_leases WHERE resource='GPU_HEAVY'").fetchone()
            return dict(row) if row else None

    def release_heavy_after_reconcile(self, operation_id: str, *, worker_stopped: bool,
                                      outputs_reconciled: bool) -> None:
        """Internal scheduler contract; never callable from a browser-supplied assertion."""
        with self.studio.db.transaction() as conn:
            row = require(conn.execute("SELECT * FROM gpu_heavy_leases WHERE resource='GPU_HEAVY' AND operation_id=?", (operation_id,)).fetchone(), "GPU lease")
            if row["state"] != "RECONCILING" or not worker_stopped or not outputs_reconciled:
                raise DomainError("GPU lease chỉ được thu hồi sau khi worker dừng và output đã đối soát.", 409)
            conn.execute("DELETE FROM gpu_heavy_leases WHERE resource='GPU_HEAVY' AND operation_id=?", (operation_id,))
