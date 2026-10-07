"""Workflow V4.5 zone B, UE and Hybrid-source branch: 07 sequences, 08-b6 retarget and Body tracks, C5.

The app compiles what Unreal has to contain from approved data and hands it to an editor script. What the
script reports is recorded per shot; it is not evidence that the result looks right. That judgement is C5,
which the operator gives after looking at the shot in Unreal.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import gpu_queue, workflow
from .domain import DomainError, canonical, digest, UE_TARGET_VERSION
from .service import now, require, uid


C5_CHECKS = ("performance", "camera", "layout")
ACTIVE = ("QUEUED", "RUNNING", "RECONCILING")


class UeBranchService:
    def __init__(self, studio, breakdown, animation, *, runner=None):
        self.studio = studio
        self.db = studio.db
        self.breakdown = breakdown
        self.animation = animation
        self.runner = runner or (lambda request: studio._unreal_adapter().build_episode(request))
        with self.db.transaction() as conn:
            conn.execute("UPDATE episode_ue_jobs SET state='RECONCILING',error='Backend khởi động lại trong lúc Unreal đang dựng; đóng Unreal cũ rồi xác nhận.',updated_at=? WHERE state='RUNNING'", (now(),))

    # ---- compile ---------------------------------------------------------------------------------------------------

    def _compile(self, conn, plan: dict) -> dict:
        """Per shot: what the assembly pass and the animation pass would write, and the hashes that identify them."""
        episode = plan["episode"]
        code = episode["code"]
        root = f"/Game/AAS/{code}"
        project_root = Path(self.studio.project(episode["project_id"])["root"]).resolve()
        slots, outdated = self.animation._slots(conn, plan)
        by_shot: dict[str, list] = {}
        for slot in slots:
            by_shot.setdefault(slot["shot_id"], []).append(slot)
        shots, retargets = [], {}
        for shot in plan["shots"]:
            if shot["route"] == "AI":
                continue
            issues = []
            if not shot["environment"]["mapped"]:
                issues.append(f"Bối cảnh {shot['environment']['display_name']} chưa có map trong thư viện.")
            issues += [f"Nhân vật {c['display_name']} chưa có asset trong thư viện." for c in shot["characters"] if not c["mapped"]]
            assemble = {"shot_id": shot["shot_id"], "sequence_path": f"{root}/Shots/{shot['shot_id']}",
                        "length_frames": shot["length_frames"], "map_path": shot["environment"]["asset_path"], "shot_size": shot["shot_size"],
                        "characters": [{"entity_key": c["entity_key"], "binding_name": "AAS_" + c["entity_key"], "asset_path": c["asset_path"]} for c in shot["characters"]],
                        "audio": [{"line_id": line["line_id"], "wav_path": str(project_root / line["relative_path"]), "sha256": line["sha256"],
                                   "sound_path": f"{root}/Audio/{line['line_id']}_{line['sha256'][:8]}", "start_frame": line["start_frame"], "frames": line["voice_frames"]}
                                  for line in shot["lines"]]}
            bindings = []
            shot_slots = sorted(by_shot.get(shot["shot_id"], []), key=lambda s: (s["character_key"], s["start_frame"]))
            for character in shot["characters"]:
                sections = []
                for slot in (s for s in shot_slots if s["character_key"] == character["entity_key"] and s["clips"]):
                    span = slot["frames"] / len(slot["clips"])
                    for position, clip in enumerate(slot["clips"]):
                        path = clip["asset_path"]
                        if clip["retarget"] in ("EXISTING", "NEEDS_CREATE"):
                            name = f"{clip['asset_name']}__{character['entity_key']}"
                            output = f"{root}/Retarget/{name}.{name}"
                            retargets[output] = {"source_animation": clip["asset_path"], "source_mesh_path": clip["source_mesh_path"], "target_mesh_path": character["body_mesh_path"],
                                                 "retargeter_path": clip["retargeter_path"], "create_retargeter_path": None if clip["retargeter_path"] else f"/Game/AAS/Retargeters/RTG_{name}",
                                                 "output_path": output}
                            path = output
                        start = slot["start_frame"] + round(position * span)
                        sections.append({"slot_id": slot["slot_id"], "animation_path": path, "start_frame": max(slot["start_frame"], start - clip["blend_frames"]),
                                         "end_frame": slot["start_frame"] + round((position + 1) * span), "blend_frames": clip["blend_frames"], "row": position % 2, "layer": "BASE"})
                    if slot["upper_body_path"]:
                        sections.append({"slot_id": slot["slot_id"], "animation_path": slot["upper_body_path"], "start_frame": slot["start_frame"], "end_frame": slot["end_frame"],
                                         "blend_frames": 0, "row": 2, "layer": "UPPER_BODY"})
                bindings.append({"entity_key": character["entity_key"], "binding_name": "AAS_" + character["entity_key"], "sections": sections})
            animate = {"shot_id": shot["shot_id"], "sequence_path": assemble["sequence_path"], "length_frames": shot["length_frames"], "bindings": bindings}
            assemble_hash = digest(assemble)
            pending = [s["slot_id"] for s in shot_slots if not s["approved"]]
            shots.append({"shot": shot, "assemble": assemble, "assemble_hash": assemble_hash, "animate": animate,
                          "animate_hash": digest({"assemble": assemble_hash, "animate": animate}), "issues": issues,
                          "needs_animation": bool(shot["characters"]), "slots_total": len(shot_slots), "slots_pending": pending})
        # The master only places the shot sequences one after another; it is tracked apart from the shots so that
        # retiming one shot does not make every later shot's build, cache and proxy stale.
        master = {"path": f"{root}/SQ_{code}_Master", "total_frames": plan["total_frames"],
                  "sections": [{"shot_id": s["shot"]["shot_id"], "sequence_path": s["assemble"]["sequence_path"], "start_frame": s["shot"]["start_frame"],
                                "length_frames": s["shot"]["length_frames"]} for s in shots]}
        return {"root": root, "master_path": master["path"], "master": master, "shots": shots, "retargets": retargets, "slots_outdated": outdated}

    def _state(self, conn, episode: dict) -> dict:
        plan = self.animation.plan(conn, episode)
        jobs = [self._job(row) for row in conn.execute("SELECT * FROM episode_ue_jobs WHERE episode_id=? ORDER BY created_at DESC LIMIT 6", (episode["id"],))]
        base = {"episode": episode, "ready": plan["ready"], "blockers": plan["blockers"], "shots": [], "jobs": jobs, "master_path": None,
                "summary": {"shots": 0, "assembled": 0, "animated": 0, "locked": 0}, "unreal_configured": self.studio.unreal_settings()["configured"]}
        if not plan["ready"]:
            return base
        compiled = self._compile(conn, plan)
        stored = {row["shot_id"]: row for row in conn.execute("SELECT * FROM ue_shot_states WHERE episode_id=?", (episode["id"],))}
        shots = []
        for item in compiled["shots"]:
            shot, row = item["shot"], stored.get(item["shot"]["shot_id"])
            assembled = "NONE" if not row or not row["assembled_hash"] else "CURRENT" if row["assembled_hash"] == item["assemble_hash"] else "STALE"
            animated = "NOT_NEEDED" if not item["needs_animation"] else "NONE" if not row or not row["animated_hash"] else "CURRENT" if row["animated_hash"] == item["animate_hash"] else "STALE"
            locked = bool(row and row["c5_hash"])
            shots.append({"shot_id": shot["shot_id"], "route": shot["route"], "description": shot["description"], "length_frames": shot["length_frames"], "seconds": shot["seconds"],
                          "sequence_path": item["assemble"]["sequence_path"], "map_path": item["assemble"]["map_path"],
                          "characters": [c["display_name"] for c in shot["characters"]], "lines": len(shot["lines"]), "vfx_tags": shot["vfx_tags"], "camera_note": shot["camera_note"],
                          "assembled": assembled, "animated": animated, "slots_total": item["slots_total"], "slots_pending": len(item["slots_pending"]),
                          "build_notes": json.loads(row["build_notes_json"]) if row and row["build_notes_json"] else [], "issues": item["issues"], "build_hash": item["animate_hash"],
                          "c5": {"locked": locked and row["c5_hash"] == item["animate_hash"], "stale": locked and row["c5_hash"] != item["animate_hash"],
                                 "notes": json.loads(row["c5_notes_json"]) if row and row["c5_notes_json"] else None},
                          "_item": item})
        summary = {"shots": len(shots), "assembled": sum(s["assembled"] == "CURRENT" for s in shots),
                   "animated": sum(s["animated"] in ("CURRENT", "NOT_NEEDED") for s in shots), "locked": sum(s["c5"]["locked"] for s in shots)}
        built = conn.execute("SELECT request_json FROM episode_ue_jobs WHERE episode_id=? AND kind='ASSEMBLE' AND state='COMPLETED' ORDER BY created_at DESC LIMIT 1", (episode["id"],)).fetchone()
        master_current = bool(built) and json.loads(built["request_json"])["master"] == compiled["master"]
        return {**base, "shots": shots, "master_path": compiled["master_path"], "summary": summary, "slots_outdated": compiled["slots_outdated"],
                "master_stale": bool(built) and not master_current,
                "_plan": plan, "_compiled": compiled,
                "manual_note": "App tạo sequence, binding nhân vật, camera mặc định, audio thoại và track Body. Blocking, camera, VFX và lip-sync chỉnh trực tiếp trong Unreal trước khi chốt C5."}

    @staticmethod
    def _public(state: dict) -> dict:
        return {**{k: v for k, v in state.items() if not k.startswith("_")},
                "shots": [{k: v for k, v in shot.items() if not k.startswith("_")} for shot in state["shots"]]}

    def state(self, episode_id: str) -> dict:
        with self.db.connect() as conn:
            return self._public(self._state(conn, self.breakdown._episode(conn, episode_id)))

    # ---- jobs ------------------------------------------------------------------------------------------------------

    @staticmethod
    def _job(row) -> dict:
        item = {key: row[key] for key in ("id", "kind", "state", "error", "created_at", "updated_at")}
        request = json.loads(row["request_json"])
        item["shots"] = [shot["shot_id"] for shot in request["shots"]]
        result = json.loads(row["result_json"]) if row["result_json"] else None
        item["failed"] = [{"shot_id": s["shot_id"], "error": s.get("error")} for s in result["shots"] if s["status"] != "OK"] if result else []
        item["retarget_notes"] = result.get("retargets", []) if result else []
        return item

    def _request(self, state: dict, kind: str, shot_ids: list[str] | None) -> dict:
        plan, compiled = state["_plan"], state["_compiled"]
        if kind == "ANIMATE" and compiled["slots_outdated"]:
            raise DomainError("Danh sách hành động chưa khớp timing hiện tại; chạy gán animation tự động (08-b1) rồi duyệt.", 409)
        chosen = []
        for shot in state["shots"]:
            if shot_ids is not None and shot["shot_id"] not in shot_ids:
                continue
            if shot["c5"]["locked"]:
                if shot_ids is not None:
                    raise DomainError(f"{shot['shot_id']} đã khóa ở C5; mở khóa trước khi dựng lại.", 409)
                continue
            if shot["issues"]:
                raise DomainError(f"{shot['shot_id']}: " + " ".join(shot["issues"]), 409)
            if kind == "ANIMATE":
                if shot["assembled"] != "CURRENT":
                    raise DomainError(f"{shot['shot_id']}: chưa tạo sequence theo timing hiện tại; chạy bước 07 trước.", 409)
                if shot["slots_pending"] or not shot["_item"]["needs_animation"]:
                    if shot_ids is not None and shot["slots_pending"]:
                        raise DomainError(f"{shot['shot_id']}: còn hành động chưa duyệt animation.", 409)
                    continue
            chosen.append(shot)
        if shot_ids is not None and {s["shot_id"] for s in chosen} != set(shot_ids):
            raise DomainError("Danh sách shot không hợp lệ hoặc không thuộc nhánh UE/Hybrid của tập.", 409)
        # With nothing to rebuild, an assembly pass may still be needed to move the shot sections of the master.
        if not chosen and not (kind == "ASSEMBLE" and state["master_stale"] and not shot_ids):
            raise DomainError("Không có shot nào cần dựng: shot đã khóa ở C5 được giữ nguyên." if kind == "ASSEMBLE" else "Không có shot nào sẵn sàng gắn animation: cần sequence đúng timing và animation đã duyệt.", 409)
        episode = plan["episode"]
        request = {"schema_version": "v45.ue-build-request.1", "kind": kind, "project_id": episode["project_id"], "episode_code": episode["code"], "fps": plan["fps"],
                   "root_path": compiled["root"],
                   "master": compiled["master"],
                   "shots": [dict(s["_item"]["assemble" if kind == "ASSEMBLE" else "animate"], item_hash=s["_item"]["assemble_hash" if kind == "ASSEMBLE" else "animate_hash"]) for s in chosen]}
        if kind == "ANIMATE":
            used = {section["animation_path"] for shot in request["shots"] for binding in shot["bindings"] for section in binding["sections"]}
            request["retargets"] = [item for output, item in sorted(compiled["retargets"].items()) if output in used]
        request["input_hash"] = digest(request)
        return request

    def _enqueue(self, episode_id: str, kind: str, shot_ids: list[str] | None) -> dict:
        self.studio._unreal_adapter()  # Fail before enqueue if Unreal is not configured.
        with self.db.transaction() as conn:
            episode = self.breakdown._episode(conn, episode_id)
            state = self._state(conn, episode)
            if not state["ready"]:
                raise DomainError("Chưa dựng được trong Unreal: " + " ".join(state["blockers"]), 409)
            active = conn.execute("SELECT state FROM episode_ue_jobs WHERE episode_id=? AND state IN ('QUEUED','RUNNING','RECONCILING') LIMIT 1", (episode_id,)).fetchone()
            if active:
                raise DomainError("Lần dựng trước bị gián đoạn; đóng Unreal cũ và xác nhận trước khi dựng lại." if active["state"] == "RECONCILING" else "Đang có việc dựng Unreal cho tập này.", 409)
            request = self._request(state, kind, shot_ids)
            request["operation_id"] = digest({"input_hash": request["input_hash"], "nonce": uid()})
            conn.execute("INSERT INTO episode_ue_jobs(id,project_id,episode_id,kind,operation_id,input_hash,request_json,state,result_json,error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'QUEUED',NULL,NULL,?,?)",
                         (uid(), episode["project_id"], episode_id, kind, request["operation_id"], request["input_hash"], canonical(request), now(), now()))
            self.studio.audit(conn, episode["project_id"], "ue_branch.queued", episode_id, {"kind": kind, "shots": len(request["shots"])})
        return self.state(episode_id)

    def enqueue_assemble(self, episode_id: str, shot_ids: list[str] | None = None) -> dict:
        return self._enqueue(episode_id, "ASSEMBLE", shot_ids)

    def enqueue_animate(self, episode_id: str, shot_ids: list[str] | None = None) -> dict:
        return self._enqueue(episode_id, "ANIMATE", shot_ids)

    @staticmethod
    def _validated(result: object, request: dict) -> dict:
        if not isinstance(result, dict) or result.get("schema_version") != "v45.ue-build-result.1" or result.get("adapter_kind") != "UNREAL_EDITOR_PYTHON" \
                or result.get("operation_id") != request["operation_id"] or result.get("status") != "APPLIED" or not isinstance(result.get("shots"), list):
            raise DomainError("Kết quả dựng từ Unreal sai schema, nguồn hoặc operation.", 409)
        if not isinstance(result.get("engine_version"), str) or not result["engine_version"].startswith(UE_TARGET_VERSION + "."):
            raise DomainError(f"App viết cho Unreal {UE_TARGET_VERSION}; lần dựng báo phiên bản {result.get('engine_version')!r}.", 409)
        wanted = {shot["shot_id"] for shot in request["shots"]}
        if {s.get("shot_id") for s in result["shots"] if isinstance(s, dict)} != wanted or any(s.get("status") not in ("OK", "FAILED") for s in result["shots"]):
            raise DomainError("Unreal báo kết quả cho danh sách shot khác với yêu cầu.", 409)
        return result

    def process_next(self) -> dict | None:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM episode_ue_jobs WHERE state='QUEUED' ORDER BY created_at LIMIT 1").fetchone()
            if not row or gpu_queue.holder(conn):
                return None
            job = dict(row)
            gpu_queue.try_acquire(conn, job["operation_id"], "UNREAL")
            conn.execute("UPDATE episode_ue_jobs SET state='RUNNING',updated_at=? WHERE id=?", (now(), job["id"]))
        request = json.loads(job["request_json"])
        try:
            with self.db.connect() as conn:
                current = self._request(self._state(conn, self.breakdown._episode(conn, job["episode_id"])), job["kind"], [s["shot_id"] for s in request["shots"]])
            if current["input_hash"] != job["input_hash"]:
                raise DomainError("Timing, mapping hoặc animation đã đổi sau khi xếp việc; xếp lại việc dựng.", 409)
            result = self._validated(self.runner(request), request)
            hashes = {shot["shot_id"]: shot for shot in request["shots"]}
            with self.db.transaction() as conn:
                for shot in result["shots"]:
                    notes = [str(n)[:300] for n in shot.get("notes", [])][:12] + ([f"Lỗi: {str(shot.get('error'))[:300]}"] if shot["status"] != "OK" else [])
                    column = "assembled_hash" if job["kind"] == "ASSEMBLE" else "animated_hash"
                    conn.execute(f"INSERT INTO ue_shot_states(episode_id,shot_id,project_id,sequence_path,{column},build_notes_json,updated_at) VALUES(?,?,?,?,?,?,?) "
                                 f"ON CONFLICT(episode_id,shot_id) DO UPDATE SET sequence_path=excluded.sequence_path,{column}=COALESCE(excluded.{column},{column}),build_notes_json=excluded.build_notes_json,updated_at=excluded.updated_at",
                                 (job["episode_id"], shot["shot_id"], job["project_id"], hashes[shot["shot_id"]]["sequence_path"],
                                  hashes[shot["shot_id"]]["item_hash"] if shot["status"] == "OK" else None, canonical(notes), now()))
                done = [s for s in result["shots"] if s["status"] == "OK"]
                conn.execute("UPDATE episode_ue_jobs SET state=?,result_json=?,error=?,updated_at=? WHERE id=?",
                             ("COMPLETED" if done or not request["shots"] else "BLOCKED", canonical(result), None if done or not request["shots"] else "Unreal không dựng được shot nào; xem lỗi từng shot.", now(), job["id"]))
                gpu_queue.release(conn, job["operation_id"])
                self.studio.audit(conn, job["project_id"], "ue_branch.completed", job["id"], {"kind": job["kind"], "ok": len(done), "failed": len(result["shots"]) - len(done)})
        except Exception as exc:
            # The editor process has ended. Every write of the script is an idempotent upsert, so a rerun is safe.
            with self.db.transaction() as conn:
                conn.execute("UPDATE episode_ue_jobs SET state='BLOCKED',error=?,updated_at=? WHERE id=?",
                             (str(exc)[:500] if isinstance(exc, DomainError) else f"{type(exc).__name__}: không dựng được trong Unreal.", now(), job["id"]))
                gpu_queue.release(conn, job["operation_id"])
        with self.db.connect() as conn:
            return self._job(conn.execute("SELECT * FROM episode_ue_jobs WHERE id=?", (job["id"],)).fetchone())

    def release_interrupted(self, job_id: str, worker_stopped: bool) -> dict:
        with self.db.transaction() as conn:
            job = require(conn.execute("SELECT * FROM episode_ue_jobs WHERE id=?", (job_id,)).fetchone(), "UE job")
            if job["state"] != "RECONCILING":
                raise DomainError("Việc dựng này không ở trạng thái cần xác nhận.", 409)
            if not worker_stopped:
                raise DomainError("Đóng cửa sổ Unreal của lần dựng cũ rồi xác nhận trong giao diện.", 409)
            conn.execute("UPDATE episode_ue_jobs SET state='BLOCKED',error='Bị gián đoạn; dựng lại khi cần.',updated_at=? WHERE id=?", (now(), job_id))
            gpu_queue.release(conn, job["operation_id"])
        return self.state(job["episode_id"])

    # ---- C5 --------------------------------------------------------------------------------------------------------

    def approve_c5(self, episode_id: str, shot_id: str, expected_hash: str, checks: dict, notes: dict) -> dict:
        """The operator has looked at the shot in Unreal and locks its motion; a locked shot is never rebuilt."""
        if not isinstance(checks, dict) or set(checks) != set(C5_CHECKS) or any(checks[key] is not True for key in C5_CHECKS):
            raise DomainError("Xác nhận đủ ba mục đã xem trong Unreal: diễn xuất, camera, bố cục và VFX.")
        if not isinstance(notes, dict) or set(notes) - {"keep", "ai_change"} or any(not isinstance(v, str) or len(v) > 400 for v in notes.values()):
            raise DomainError("Ghi chú phần giữ nguyên và phần AI sửa tối đa 400 ký tự mỗi mục.")
        with self.db.transaction() as conn:
            episode = self.breakdown._episode(conn, episode_id)
            state = self._state(conn, episode)
            shot = require(next((s for s in state["shots"] if s["shot_id"] == shot_id), None), "shot")
            if shot["build_hash"] != expected_hash:
                raise DomainError("Shot đã đổi sau khi bạn xem; tải lại.", 409)
            if shot["assembled"] != "CURRENT" or shot["animated"] not in ("CURRENT", "NOT_NEEDED"):
                raise DomainError("Chưa chốt được: sequence hoặc animation trong Unreal chưa đúng với timing và animation đã duyệt.", 409)
            if shot["c5"]["locked"]:
                raise DomainError("Shot này đã khóa ở C5.", 409)
            clean = {key: notes.get(key, "").strip() for key in ("keep", "ai_change")}
            if shot["route"] == "HYBRID" and not (clean["keep"] and clean["ai_change"]):
                raise DomainError("Shot Hybrid cần ghi rõ phần giữ nguyên từ UE và phần để AI sửa.", 409)
            sequence = conn.execute("SELECT COALESCE(MAX(exact_version),0)+1 FROM workflow_checkpoints WHERE project_id=? AND checkpoint='C5' AND scope_id=?", (episode["project_id"], shot_id)).fetchone()[0]
            workflow.record_checkpoint(self.studio, conn, episode["project_id"], "C5", shot_id, sequence, expected_hash,
                                       {"branch": shot["route"], "episode_id": episode_id, "sequence_path": shot["sequence_path"], "checks": list(C5_CHECKS), "notes": clean,
                                        "evidence": "OPERATOR_VIEWED_IN_UNREAL"})
            conn.execute("UPDATE ue_shot_states SET c5_hash=?,c5_notes_json=?,updated_at=? WHERE episode_id=? AND shot_id=?", (expected_hash, canonical(clean), now(), episode_id, shot_id))
        return self.state(episode_id)

    def unlock_c5(self, episode_id: str, shot_id: str) -> dict:
        with self.db.transaction() as conn:
            episode = self.breakdown._episode(conn, episode_id)
            if not conn.execute("UPDATE ue_shot_states SET c5_hash=NULL,updated_at=? WHERE episode_id=? AND shot_id=? AND c5_hash IS NOT NULL", (now(), episode_id, shot_id)).rowcount:
                raise DomainError("Shot này chưa khóa ở C5.", 409)
            self.studio.audit(conn, episode["project_id"], "ue_branch.c5_unlocked", episode_id, {"shot": shot_id})
        return self.state(episode_id)
