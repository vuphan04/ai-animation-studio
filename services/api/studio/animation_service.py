"""Workflow V4.5 step 08-b1..b5: assign library animations to character actions, and bridge missing motion to Kimodo.

Nothing here writes to Unreal. Kimodo itself runs outside the app: the app prepares the requests, the
operator imports the results into the UE project, and the chosen clip is registered in the series library.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import shot_plan
from .animation_domain import DEFAULT_TAG, LOW_BELOW, basis, clean_clips, layout, match, retarget_mode
from .breakdown_domain import TAG
from .domain import DomainError, canonical
from .service import now, require, uid


VARIANTS = 3


def _like(folder: str) -> str:
    return folder.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"


class AnimationService:
    def __init__(self, studio, breakdown, routes, timing):
        self.studio = studio
        self.db = studio.db
        self.breakdown = breakdown
        self.routes = routes
        self.timing = timing

    def plan(self, conn, episode: dict) -> dict:
        return shot_plan.load(self.routes, self.timing, conn, episode)

    # ---- library lookups -------------------------------------------------------------------------------------------

    def _candidates(self, conn, plan: dict) -> tuple[list[dict], dict]:
        """Animations inside the library's folders plus clips approved for the series, with the tags those answer to."""
        library_tags = {r["asset_path"]: set(json.loads(r["tags_json"])) for r in conn.execute(
            "SELECT asset_path,tags_json FROM series_motion_clips WHERE project_id=?", (plan["episode"]["project_id"],))}
        found: dict[str, dict] = {}
        for folder in plan["animation_folders"]:
            for row in conn.execute("SELECT object_path,asset_name,skeleton_path FROM ue_scan_assets WHERE scan_id=? AND category='animation' AND (package_path=? OR package_path LIKE ? ESCAPE '\\')",
                                    (plan["scan_id"], folder, _like(folder))):
                found[row["object_path"]] = dict(row)
        for path in library_tags:
            row = self._animation(conn, plan, path)
            if row:
                found[path] = row
        return list(found.values()), library_tags

    @staticmethod
    def _animation(conn, plan: dict, path: object) -> dict | None:
        row = conn.execute("SELECT object_path,asset_name,skeleton_path FROM ue_scan_assets WHERE scan_id=? AND category='animation' AND object_path=?",
                           (plan["scan_id"], path)).fetchone() if isinstance(path, str) else None
        return dict(row) if row else None

    @staticmethod
    def _source_mesh(conn, plan: dict, skeleton: str | None) -> str | None:
        row = conn.execute("SELECT object_path FROM ue_scan_assets WHERE scan_id=? AND category='skeletal_mesh' AND skeleton_path=? ORDER BY object_path LIMIT 1",
                           (plan["scan_id"], skeleton)).fetchone() if skeleton else None
        return row["object_path"] if row else None

    @staticmethod
    def _expected(plan: dict) -> dict:
        """Slots the approved shots call for: every mapped character of every UE or Hybrid shot."""
        slots = {}
        for shot in plan["shots"]:
            if shot["route"] == "AI":
                continue
            for character in shot["characters"]:
                if not character["mapped"]:
                    continue
                for slot in layout(shot, character):
                    slots[slot["slot_id"]] = {**slot, "basis": basis(slot, character["skeleton_path"]), "target_skeleton": character["skeleton_path"],
                                              "character_name": character["display_name"], "action": character["action"],
                                              "inferred": not character["motion_tags"], "route": shot["route"]}
        return slots

    def _describe(self, conn, plan: dict, row, expected: dict | None) -> dict:
        clips, issues = [], []
        target = expected["target_skeleton"] if expected else None
        for clip in json.loads(row["clips_json"]):
            asset = self._animation(conn, plan, clip["asset_path"])
            item = {**clip, "asset_name": asset["asset_name"] if asset else clip["asset_path"].rsplit(".", 1)[-1], "skeleton_path": asset["skeleton_path"] if asset else None,
                    "retarget": None, "retargeter_path": None, "source_mesh_path": None}
            if not asset:
                issues.append(f"{item['asset_name']}: không còn trong lần quét asset mới nhất.")
            else:
                mode = retarget_mode(asset["skeleton_path"], target, row["character_key"], plan["retarget_pairs"])
                item.update(retarget=mode["mode"], retargeter_path=mode["retargeter_path"])
                if mode["mode"] == "UNDECIDED":
                    issues.append(f"{item['asset_name']}: chưa có quyết định retarget từ skeleton nguồn sang nhân vật này; chọn ở CS1 rồi duyệt lại thư viện.")
                elif mode["mode"] != "DIRECT":
                    item["source_mesh_path"] = self._source_mesh(conn, plan, asset["skeleton_path"])
                    if not item["source_mesh_path"]:
                        issues.append(f"{item['asset_name']}: lần quét không có Skeletal Mesh nào dùng skeleton nguồn, nên chưa retarget được.")
            clips.append(item)
        stale = expected is None or expected["basis"] != row["basis_hash"]
        return {"slot_id": row["slot_id"], "shot_id": row["shot_id"], "character_key": row["character_key"],
                "character_name": expected["character_name"] if expected else row["character_key"], "action": expected["action"] if expected else "",
                "route": expected["route"] if expected else None, "tag": row["tag"], "inferred_tag": bool(expected and expected["inferred"]),
                "start_frame": row["start_frame"], "end_frame": row["end_frame"], "frames": row["end_frame"] - row["start_frame"],
                "clips": clips, "upper_body_path": row["upper_body_path"], "source": row["source"], "confidence": row["confidence"],
                "low_confidence": row["status"] == "ASSIGNED" and row["confidence"] < LOW_BELOW, "status": row["status"],
                "approved": bool(row["approved"]) and not stale and not issues, "stale": stale, "issues": issues, "revision": row["revision"]}

    def _slots(self, conn, plan: dict) -> tuple[list[dict], bool]:
        expected = self._expected(plan)
        rows = conn.execute("SELECT * FROM animation_slots WHERE episode_id=? ORDER BY shot_id,character_key,position", (plan["episode"]["id"],)).fetchall()
        slots = [self._describe(conn, plan, row, expected.get(row["slot_id"])) for row in rows]
        outdated = {row["slot_id"] for row in rows} != set(expected) or any(slot["stale"] for slot in slots)
        return slots, outdated

    def state(self, episode_id: str) -> dict:
        with self.db.connect() as conn:
            episode = self.breakdown._episode(conn, episode_id)
            plan = self.plan(conn, episode)
            if not plan["ready"]:
                return {"episode": episode, "ready": False, "blockers": plan["blockers"], "slots": [], "outdated": False, "requests": [],
                        "summary": {"slots": 0, "approved": 0, "missing": 0, "kimodo": 0, "low": 0}, "allowed": False, "folders": [], "library_clips": []}
            slots, outdated = self._slots(conn, plan)
            requests = [self._request(conn, row) for row in conn.execute("SELECT * FROM kimodo_requests WHERE episode_id=? ORDER BY created_at", (episode_id,))]
            clips = [{"asset_path": r["asset_path"], "tags": json.loads(r["tags_json"]), "source": r["source"]} for r in conn.execute(
                "SELECT * FROM series_motion_clips WHERE project_id=? ORDER BY asset_path", (episode["project_id"],))]
        # 08-b2: what needs the operator first — missing, then the least certain matches.
        slots.sort(key=lambda s: (s["approved"], {"MISSING": 0, "KIMODO": 1, "ASSIGNED": 2}[s["status"]], 0 if s["approved"] else s["confidence"], s["slot_id"]))
        summary = {"slots": len(slots), "approved": sum(s["approved"] for s in slots), "missing": sum(s["status"] == "MISSING" for s in slots),
                   "kimodo": sum(s["status"] == "KIMODO" for s in slots), "low": sum(s["low_confidence"] and not s["approved"] for s in slots)}
        return {"episode": episode, "ready": True, "blockers": [], "slots": slots, "outdated": outdated, "requests": requests, "summary": summary,
                "allowed": bool(slots) and not outdated and summary["approved"] == len(slots), "folders": plan["animation_folders"], "library_clips": clips,
                "preview_note": "App chưa có ảnh xem trước animation; mở asset trong Unreal để xem trước khi duyệt.",
                "kimodo_note": "Kimodo chạy ngoài app: xuất danh sách yêu cầu, tạo motion bằng kimodo_gen hoặc Animotive, import vào project Unreal, quét lại asset rồi gắn phương án ở đây."}

    # ---- 08-b1 automatic assignment --------------------------------------------------------------------------------

    def _ready(self, conn, episode_id: str) -> tuple[dict, dict]:
        episode = self.breakdown._episode(conn, episode_id)
        plan = self.plan(conn, episode)
        if not plan["ready"]:
            raise DomainError("Chưa gán được animation: " + " ".join(plan["blockers"]), 409)
        return episode, plan

    def propose(self, episode_id: str) -> dict:
        """Create or refresh the slots. Approved, manual and Kimodo slots whose action did not change are kept."""
        with self.db.transaction() as conn:
            episode, plan = self._ready(conn, episode_id)
            expected = self._expected(plan)
            candidates, library_tags = self._candidates(conn, plan)
            existing = {row["slot_id"]: row for row in conn.execute("SELECT * FROM animation_slots WHERE episode_id=?", (episode_id,))}
            for slot_id, slot in expected.items():
                row = existing.pop(slot_id, None)
                decided = bool(row) and (row["approved"] or row["source"] != "AUTO")
                if decided and row["basis_hash"] == slot["basis"]:
                    continue
                if decided and row["tag"] == slot["tag"] and row["basis_hash"] == basis(dict(row), slot["target_skeleton"]) \
                        and all(clip["blend_frames"] <= slot["end_frame"] - slot["start_frame"] for clip in json.loads(row["clips_json"])):
                    # Same action on the same skeleton, only its frames moved (a retimed shot): what the operator chose and approved stays.
                    conn.execute("UPDATE animation_slots SET start_frame=?,end_frame=?,basis_hash=?,revision=revision+1,updated_at=? WHERE episode_id=? AND slot_id=?",
                                 (slot["start_frame"], slot["end_frame"], slot["basis"], now(), episode_id, slot_id))
                    continue
                found = match(slot["tag"], candidates, slot["target_skeleton"], slot["character_key"], plan["retarget_pairs"], library_tags)
                conn.execute("INSERT INTO animation_slots(episode_id,slot_id,project_id,shot_id,character_key,position,tag,start_frame,end_frame,basis_hash,clips_json,upper_body_path,source,confidence,status,approved,revision,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL,'AUTO',?,?,0,1,?) "
                             "ON CONFLICT(episode_id,slot_id) DO UPDATE SET tag=excluded.tag,start_frame=excluded.start_frame,end_frame=excluded.end_frame,basis_hash=excluded.basis_hash,clips_json=excluded.clips_json,upper_body_path=NULL,source='AUTO',confidence=excluded.confidence,status=excluded.status,approved=0,revision=revision+1,updated_at=excluded.updated_at",
                             (episode_id, slot_id, episode["project_id"], slot["shot_id"], slot["character_key"], slot["position"], slot["tag"], slot["start_frame"], slot["end_frame"],
                              slot["basis"], canonical([{"asset_path": found["asset_path"], "blend_frames": 0}] if found else []), found["confidence"] if found else 0.0,
                              "ASSIGNED" if found else "MISSING", now()))
                conn.execute("DELETE FROM kimodo_requests WHERE episode_id=? AND slot_id=? AND state!='APPROVED'", (episode_id, slot_id))
            for slot_id in existing:
                conn.execute("DELETE FROM animation_slots WHERE episode_id=? AND slot_id=?", (episode_id, slot_id))
                conn.execute("DELETE FROM kimodo_requests WHERE episode_id=? AND slot_id=? AND state!='APPROVED'", (episode_id, slot_id))
            self.studio.audit(conn, episode["project_id"], "animation.proposed", episode_id, {"slots": len(expected), "candidates": len(candidates)})
        return self.state(episode_id)

    # ---- 08-b2 review, 08-b3 composition ---------------------------------------------------------------------------

    def _row(self, conn, episode_id: str, slot_id: str, expected_revision: int):
        row = require(conn.execute("SELECT * FROM animation_slots WHERE episode_id=? AND slot_id=?", (episode_id, slot_id)).fetchone(), "animation slot")
        if row["revision"] != expected_revision:
            raise DomainError("Hành động này đã đổi ở phiên khác; tải lại.", 409)
        return row

    def assign(self, episode_id: str, slot_id: str, expected_revision: int, clips: list, upper_body_path: str | None) -> dict:
        """Operator's own choice from the scanned animation list: one clip, a chain with blends, or a layered upper body."""
        with self.db.transaction() as conn:
            episode, plan = self._ready(conn, episode_id)
            row = self._row(conn, episode_id, slot_id, expected_revision)
            clean = clean_clips(clips, row["end_frame"] - row["start_frame"])
            for path in [clip["asset_path"] for clip in clean] + ([upper_body_path] if upper_body_path else []):
                if not self._animation(conn, plan, path):
                    raise DomainError("Chọn animation trong danh sách đã quét của project Unreal; không nhập đường dẫn tự do.", 409)
            conn.execute("UPDATE animation_slots SET clips_json=?,upper_body_path=?,source='MANUAL',confidence=1.0,status='ASSIGNED',approved=0,revision=revision+1,updated_at=? WHERE episode_id=? AND slot_id=?",
                         (canonical(clean), upper_body_path or None, now(), episode_id, slot_id))
            conn.execute("DELETE FROM kimodo_requests WHERE episode_id=? AND slot_id=? AND state!='APPROVED'", (episode_id, slot_id))
            self.studio.audit(conn, episode["project_id"], "animation.assigned", episode_id, {"slot": slot_id, "clips": len(clean)})
        return self.state(episode_id)

    def _approve(self, conn, plan: dict, row) -> None:
        slot = self._describe(conn, plan, row, self._expected(plan).get(row["slot_id"]))
        if slot["status"] != "ASSIGNED" or slot["stale"] or slot["issues"]:
            reason = slot["issues"][0] if slot["issues"] else "hành động đã đổi theo timing mới; chạy lại gán tự động." if slot["stale"] else "chưa có animation."
            raise DomainError(f"Chưa duyệt được {row['slot_id']}: {reason}", 409)
        conn.execute("UPDATE animation_slots SET approved=1,revision=revision+1,updated_at=? WHERE episode_id=? AND slot_id=?", (now(), row["episode_id"], row["slot_id"]))

    def approve(self, episode_id: str, slot_id: str, expected_revision: int) -> dict:
        with self.db.transaction() as conn:
            episode, plan = self._ready(conn, episode_id)
            self._approve(conn, plan, self._row(conn, episode_id, slot_id, expected_revision))
            self.studio.audit(conn, episode["project_id"], "animation.approved", episode_id, {"slot": slot_id})
        return self.state(episode_id)

    def approve_confident(self, episode_id: str) -> dict:
        """Approve every automatic match that is not flagged as low confidence; the rest stay for one-by-one review."""
        approved = 0
        with self.db.transaction() as conn:
            episode, plan = self._ready(conn, episode_id)
            slots, _ = self._slots(conn, plan)
            for slot in slots:
                if slot["status"] == "ASSIGNED" and not slot["approved"] and not slot["low_confidence"] and not slot["stale"] and not slot["issues"]:
                    conn.execute("UPDATE animation_slots SET approved=1,revision=revision+1,updated_at=? WHERE episode_id=? AND slot_id=?", (now(), episode_id, slot["slot_id"]))
                    approved += 1
            self.studio.audit(conn, episode["project_id"], "animation.approved_confident", episode_id, {"slots": approved})
        return {**self.state(episode_id), "bulk_approved": approved}

    # ---- 08-b4 / 08-b5 Kimodo bridge -------------------------------------------------------------------------------

    @staticmethod
    def _request(conn, row) -> dict:
        return {"id": row["id"], "slot_id": row["slot_id"], "prompt": row["prompt"], "seconds": row["seconds"], "variants": row["variants"],
                "state": row["state"], "variant_paths": json.loads(row["variants_json"]), "selected_path": row["selected_path"], "revision": row["revision"]}

    def to_kimodo(self, episode_id: str, slot_id: str, expected_revision: int) -> dict:
        with self.db.transaction() as conn:
            episode, plan = self._ready(conn, episode_id)
            row = self._row(conn, episode_id, slot_id, expected_revision)
            shot = plan["by_id"].get(row["shot_id"])
            if not shot:
                raise DomainError("Shot của hành động này không còn trong kế hoạch; chạy lại gán tự động.", 409)
            seconds = round((row["end_frame"] - row["start_frame"]) * plan["fps"]["den"] / plan["fps"]["num"], 2)
            motion = "stands idle" if row["tag"] == DEFAULT_TAG else f"performs this motion: {row['tag']}"
            prompt = f"A person {motion}." + (f" Scene context: {shot['description_en']}" if shot["description_en"] else "")
            conn.execute("UPDATE animation_slots SET clips_json='[]',upper_body_path=NULL,source='KIMODO',confidence=0,status='KIMODO',approved=0,revision=revision+1,updated_at=? WHERE episode_id=? AND slot_id=?",
                         (now(), episode_id, slot_id))
            conn.execute("INSERT INTO kimodo_requests(id,project_id,episode_id,slot_id,prompt,seconds,variants,state,variants_json,selected_path,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'PENDING','[]',NULL,1,?,?) "
                         "ON CONFLICT(episode_id,slot_id) DO UPDATE SET prompt=excluded.prompt,seconds=excluded.seconds,state='PENDING',variants_json='[]',selected_path=NULL,revision=revision+1,updated_at=excluded.updated_at",
                         (uid(), episode["project_id"], episode_id, slot_id, prompt, seconds, VARIANTS, now(), now()))
            self.studio.audit(conn, episode["project_id"], "animation.kimodo_requested", episode_id, {"slot": slot_id, "seconds": seconds})
        return self.state(episode_id)

    def _kimodo(self, conn, request_id: str, expected_revision: int):
        row = require(conn.execute("SELECT * FROM kimodo_requests WHERE id=?", (request_id,)).fetchone(), "Kimodo request")
        if row["revision"] != expected_revision:
            raise DomainError("Yêu cầu Kimodo này đã đổi ở phiên khác; tải lại.", 409)
        return row

    def save_request(self, request_id: str, expected_revision: int, prompt: str) -> dict:
        """Edit the prompt, which also sends the request back for a new round when the variants were not good enough."""
        if not isinstance(prompt, str) or not 3 <= len(prompt.strip()) <= 600:
            raise DomainError("Prompt Kimodo cần từ 3 đến 600 ký tự.")
        with self.db.transaction() as conn:
            row = self._kimodo(conn, request_id, expected_revision)
            if row["state"] == "APPROVED":
                raise DomainError("Motion này đã được duyệt và lưu vào thư viện.", 409)
            conn.execute("UPDATE kimodo_requests SET prompt=?,state='PENDING',variants_json='[]',revision=revision+1,updated_at=? WHERE id=?", (prompt.strip(), now(), request_id))
        return self.state(row["episode_id"])

    def export_requests(self, episode_id: str) -> dict:
        with self.db.connect() as conn:
            episode, plan = self._ready(conn, episode_id)
            rows = conn.execute("SELECT r.*,s.shot_id,s.character_key,s.tag FROM kimodo_requests r JOIN animation_slots s ON s.episode_id=r.episode_id AND s.slot_id=r.slot_id WHERE r.episode_id=? AND r.state='PENDING' ORDER BY r.slot_id", (episode_id,)).fetchall()
        if not rows:
            raise DomainError("Không có yêu cầu Kimodo nào đang chờ tạo.", 409)
        relative = f"manifests/kimodo-{episode['code']}.json"
        payload = {"schema_version": "kimodo.batch.1", "episode": episode["code"], "fps": plan["fps"],
                   "requests": [{"request_id": r["id"], "slot_id": r["slot_id"], "shot_id": r["shot_id"], "character_key": r["character_key"], "tag": r["tag"],
                                 "prompt": r["prompt"], "seconds": r["seconds"], "variants": r["variants"], "output_name": f"KM_{r['slot_id']}"} for r in rows]}
        root = Path(self.studio.project(episode["project_id"])["root"]).resolve()
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"relative_path": relative, "requests": len(rows)}

    def attach_variants(self, request_id: str, expected_revision: int, asset_paths: list) -> dict:
        if not isinstance(asset_paths, list) or not 1 <= len(asset_paths) <= VARIANTS or len(set(asset_paths)) != len(asset_paths):
            raise DomainError(f"Gắn từ 1 đến {VARIANTS} phương án khác nhau.")
        with self.db.transaction() as conn:
            row = self._kimodo(conn, request_id, expected_revision)
            episode, plan = self._ready(conn, row["episode_id"])
            if row["state"] == "APPROVED" or any(not self._animation(conn, plan, path) for path in asset_paths):
                raise DomainError("Chọn các animation Kimodo đã import trong lần quét asset mới nhất; yêu cầu đã duyệt thì không gắn lại.", 409)
            conn.execute("UPDATE kimodo_requests SET state='RESULTS_ATTACHED',variants_json=?,revision=revision+1,updated_at=? WHERE id=?", (canonical(asset_paths), now(), request_id))
            self.studio.audit(conn, episode["project_id"], "animation.kimodo_attached", row["episode_id"], {"slot": row["slot_id"], "variants": len(asset_paths)})
        return self.state(row["episode_id"])

    def approve_variant(self, request_id: str, expected_revision: int, asset_path: str, tags: list) -> dict:
        """08-b5: one variant becomes the slot's animation and joins the series library under the given tags."""
        if not isinstance(tags, list) or not 1 <= len(tags) <= 10 or any(not isinstance(tag, str) or not TAG.fullmatch(tag) for tag in tags):
            raise DomainError("Cần 1 đến 10 nhãn tiếng Anh viết thường để các tập sau tìm lại clip này.")
        with self.db.transaction() as conn:
            row = self._kimodo(conn, request_id, expected_revision)
            episode, plan = self._ready(conn, row["episode_id"])
            if row["state"] != "RESULTS_ATTACHED" or asset_path not in json.loads(row["variants_json"]) or not self._animation(conn, plan, asset_path):
                raise DomainError("Chọn một trong các phương án đã gắn và còn trong lần quét mới nhất.", 409)
            conn.execute("INSERT OR REPLACE INTO series_motion_clips(project_id,asset_path,tags_json,source,origin_slot_id,created_at) VALUES(?,?,?,'KIMODO',?,?)",
                         (episode["project_id"], asset_path, canonical(sorted(set(tags))), row["slot_id"], now()))
            conn.execute("UPDATE kimodo_requests SET state='APPROVED',selected_path=?,revision=revision+1,updated_at=? WHERE id=?", (asset_path, now(), request_id))
            conn.execute("UPDATE animation_slots SET clips_json=?,source='KIMODO',confidence=1.0,status='ASSIGNED',approved=0,revision=revision+1,updated_at=? WHERE episode_id=? AND slot_id=?",
                         (canonical([{"asset_path": asset_path, "blend_frames": 0}]), now(), row["episode_id"], row["slot_id"]))
            slot = conn.execute("SELECT * FROM animation_slots WHERE episode_id=? AND slot_id=?", (row["episode_id"], row["slot_id"])).fetchone()
            described = self._describe(conn, plan, slot, self._expected(plan).get(row["slot_id"]))
            if not described["stale"] and not described["issues"]:
                conn.execute("UPDATE animation_slots SET approved=1 WHERE episode_id=? AND slot_id=?", (row["episode_id"], row["slot_id"]))
            self.studio.audit(conn, episode["project_id"], "animation.kimodo_approved", row["episode_id"], {"slot": row["slot_id"], "tags": sorted(set(tags))})
        return self.state(row["episode_id"])
