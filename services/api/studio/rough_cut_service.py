"""Workflow V4.5 checkpoint C7: rough cut per scene and Picture Lock.

A rough cut joins what C6 fixed for every shot (UE proxy, AI or Hybrid draft) at the exact shot lengths of
C4, with the approved dialogue. Locking a scene pins exactly those inputs; only shots of a locked scene
may later be rendered or generated as finals.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from . import workflow
from .domain import DomainError, canonical, digest, file_sha256
from .media_tools import MediaTools
from .proxy_service import proxy_size
from .route_service import RouteService
from .service import now, require, uid
from .timing_domain import to_seconds


CHECKS = ("rhythm", "continuity", "eyelines", "look")


class RoughCutService:
    def __init__(self, studio, breakdown, animation, proxy, ai, timing, *, media=None):
        self.studio = studio
        self.db = studio.db
        self.breakdown = breakdown
        self.animation = animation
        self.proxy = proxy
        self.ai = ai
        self.timing = timing
        self.media = media or MediaTools(studio)
        with self.db.transaction() as conn:
            # Nothing outside the app is touched by a rough cut, so an interrupted one can simply be run again.
            conn.execute("UPDATE rough_cut_jobs SET state='BLOCKED',error='Backend khởi động lại trong lúc ghép; ghép lại.',updated_at=? WHERE state='RUNNING'", (now(),))

    # ---- state -----------------------------------------------------------------------------------------------------

    def _state(self, conn, episode: dict) -> dict:
        plan = self.animation.plan(conn, episode)
        jobs = [dict(row) for row in conn.execute("SELECT id,scene_key,state,error,updated_at FROM rough_cut_jobs WHERE episode_id=? ORDER BY created_at DESC LIMIT 6", (episode["id"],))]
        base = {"episode": episode, "ready": plan["ready"], "blockers": plan["blockers"], "scenes": [], "jobs": jobs, "ffmpeg_configured": self.media.available(),
                "whole": None, "c7": None, "summary": {"scenes": 0, "locked": 0, "shots": 0, "shots_ready": 0}, "approve_blockers": list(plan["blockers"]), "allowed": False}
        if not plan["ready"]:
            return base
        root = Path(self.studio.project(episode["project_id"])["root"]).resolve()
        fps = plan["fps"]
        width, height = proxy_size(conn, episode["project_id"])
        ue_state, ai_state = self.proxy._state(conn, episode), self.ai._state(conn, episode)
        ue = {shot["shot_id"]: shot for shot in ue_state["shots"]}
        ai = {shot["shot_id"]: shot for shot in ai_state["shots"]}
        takes = {row["id"]: row for row in conn.execute("SELECT id,version,relative_path,sha256 FROM ai_video_takes WHERE episode_id=?", (episode["id"],))}
        cuts = {row["scene_key"]: row for row in conn.execute("SELECT * FROM rough_cuts WHERE episode_id=?", (episode["id"],))}
        locks = {row["scene_key"]: row for row in conn.execute("SELECT * FROM scene_picture_locks WHERE episode_id=?", (episode["id"],))}

        def source(shot: dict) -> dict:
            if shot["route"] == "UE":
                item = ue.get(shot["shot_id"])
                if item and item["c6"]["locked"]:
                    return {"ready": True, "kind": "UE_PROXY", "label": f"Proxy UE bản {item['proxy']['version']}", "relative_path": item["proxy"]["relative_path"], "sha256": item["proxy"]["sha256"]}
                return {"ready": False, "kind": "UE_PROXY", "label": "Nhánh UE: chưa chốt cache và proxy (C6)"}
            item = ai.get(shot["shot_id"])
            take = takes.get(item["c6"]["take_id"]) if item and item["c6"]["locked"] else None
            if take:
                return {"ready": True, "kind": "AI_DRAFT", "label": f"Draft {'Hybrid' if shot['route'] == 'HYBRID' else 'AI'} bản {take['version']}", "relative_path": take["relative_path"], "sha256": take["sha256"]}
            return {"ready": False, "kind": "AI_DRAFT", "label": ("Nhánh Hybrid" if shot["route"] == "HYBRID" else "Nhánh AI") + ": chưa chốt draft (C6)"}

        def cut_view(key: str, input_hash: str) -> dict | None:
            row = cuts.get(key)
            if not row:
                return None
            return {"id": row["id"], "created_at": row["created_at"], "frames": row["frames"], "seconds": to_seconds(row["frames"], fps), "sha256": row["sha256"],
                    "current": row["input_hash"] == input_hash and (root / row["relative_path"]).is_file()}

        scenes = []
        for shot in plan["shots"]:
            if not scenes or scenes[-1]["scene_key"] != shot["scene_key"]:
                scenes.append({"scene_key": shot["scene_key"], "heading": shot["scene_heading"], "frames": 0, "shots": []})
            scene = scenes[-1]
            scene["shots"].append({"shot_id": shot["shot_id"], "route": shot["route"], "length_frames": shot["length_frames"], "start_frame": scene["frames"],
                                   "start_seconds": to_seconds(scene["frames"], fps), "source": source(shot),
                                   "audio": [[line["line_id"], line["sha256"], line["start_frame"]] for line in shot["lines"]]})
            scene["frames"] += shot["length_frames"]
        for scene in scenes:
            scene["ready"] = all(shot["source"]["ready"] for shot in scene["shots"])
            scene["seconds"] = to_seconds(scene["frames"], fps)
            scene["input_hash"] = digest({"fps": fps, "size": [width, height], "shots": [[s["shot_id"], s["length_frames"], s["source"].get("sha256"), s["audio"]] for s in scene["shots"]]})
            scene["rough_cut"] = cut_view(scene["scene_key"], scene["input_hash"])
            lock = locks.get(scene["scene_key"])
            current = bool(lock) and lock["lock_hash"] == scene["input_hash"] and scene["ready"]
            scene["lock"] = {"locked": current, "stale": not current, "created_at": lock["created_at"], "checks": json.loads(lock["checks_json"])} if lock else None
        whole_hash = digest([scene["input_hash"] for scene in scenes])
        locked = [scene for scene in scenes if scene["lock"] and scene["lock"]["locked"]]
        lock_hash = digest({scene["scene_key"]: scene["input_hash"] for scene in scenes})
        latest = RouteService._checkpoint(conn, episode, "C7")
        c7 = {"sequence": latest["exact_version"], "created_at": latest["created_at"], "decision": json.loads(latest["decision_json"]),
              "current": latest["dependency_hash"] == lock_hash and len(locked) == len(scenes)} if latest else None
        approve = []
        if len(locked) < len(scenes):
            approve.append(f"Còn {len(scenes) - len(locked)} scene chưa khóa dựng.")
        shots = [shot for scene in scenes for shot in scene["shots"]]
        return {**base, "scenes": scenes, "fps": fps, "size": {"width": width, "height": height},
                "whole": {"ready": all(scene["ready"] for scene in scenes), "input_hash": whole_hash, "frames": sum(scene["frames"] for scene in scenes),
                          "seconds": to_seconds(sum(scene["frames"] for scene in scenes), fps), "rough_cut": cut_view("", whole_hash)},
                "c7": c7, "lock_hash": lock_hash, "approve_blockers": approve, "allowed": not approve,
                "summary": {"scenes": len(scenes), "locked": len(locked), "shots": len(shots), "shots_ready": sum(s["source"]["ready"] for s in shots)},
                "final_note": "Chỉ shot thuộc scene đã khóa dựng mới được render hoặc tạo final. Chưa đạt thì mở khóa C5 hoặc C6 của shot ở nhánh tương ứng, sửa rồi ghép lại.",
                "_plan": plan, "_root": root, "_ue": ue, "_ai": ai, "_unreal_configured": ue_state["unreal_configured"]}

    @staticmethod
    def _public(state: dict) -> dict:
        return {k: v for k, v in state.items() if not k.startswith("_")}

    @staticmethod
    def _segment(root: Path, episode_code: str, shot: dict, fps: dict, size: dict) -> Path:
        """Where the conformed review copy of a shot lives; the name changes whenever its source or length does."""
        key = digest({"source": shot["source"]["sha256"], "frames": shot["length_frames"], "fps": fps, "size": size})[:16]
        return root / f"media/roughcut/{episode_code}/segments/{shot['shot_id']}-{key}.mp4"

    def segment_file(self, episode_id: str, shot_id: str) -> Path:
        """The shot exactly as it appears in the rough cut, for comparing a final against it."""
        with self.db.connect() as conn:
            episode = self.breakdown._episode(conn, episode_id)
            state = self._state(conn, episode)
        shot = require(next((s for scene in state["scenes"] for s in scene["shots"] if s["shot_id"] == shot_id), None), "shot")
        path = self._segment(state["_root"], episode["code"], shot, state["fps"], state["size"]) if shot["source"]["ready"] else None
        if not path or not path.is_file():
            raise DomainError("Chưa có bản rough cut của shot này; ghép rough cut của scene trước.", 409)
        return path

    def state(self, episode_id: str) -> dict:
        with self.db.connect() as conn:
            return self._public(self._state(conn, self.breakdown._episode(conn, episode_id)))

    def locked_shots(self, conn, episode: dict) -> set[str]:
        """Shots a final may be made for: those of scenes whose Picture Lock still matches what is locked at C6."""
        return {shot["shot_id"] for scene in self._state(conn, episode)["scenes"] if scene["lock"] and scene["lock"]["locked"] for shot in scene["shots"]}

    # ---- assembling ------------------------------------------------------------------------------------------------

    def _scope(self, state: dict, scene_key: str | None) -> tuple[list[dict], str]:
        if scene_key:
            scene = require(next((s for s in state["scenes"] if s["scene_key"] == scene_key), None), "scene")
            scenes, input_hash = [scene], scene["input_hash"]
        else:
            scenes, input_hash = state["scenes"], state["whole"]["input_hash"]
        missing = [shot["shot_id"] for scene in scenes for shot in scene["shots"] if not shot["source"]["ready"]]
        if missing:
            raise DomainError("Chưa ghép được rough cut: còn shot chưa chốt C6 (" + ", ".join(missing[:6]) + ("…" if len(missing) > 6 else "") + ").", 409)
        return [shot for scene in scenes for shot in scene["shots"]], input_hash

    def enqueue(self, episode_id: str, scene_key: str | None) -> dict:
        self.media.paths()
        with self.db.transaction() as conn:
            episode = self.breakdown._episode(conn, episode_id)
            state = self._state(conn, episode)
            if not state["ready"]:
                raise DomainError("Chưa ghép được rough cut: " + " ".join(state["blockers"]), 409)
            _, input_hash = self._scope(state, scene_key)
            if conn.execute("SELECT 1 FROM rough_cut_jobs WHERE episode_id=? AND scene_key=? AND state IN ('QUEUED','RUNNING')", (episode_id, scene_key or "")).fetchone():
                raise DomainError("Đang ghép rough cut cho phạm vi này.", 409)
            conn.execute("INSERT INTO rough_cut_jobs(id,project_id,episode_id,scene_key,operation_id,input_hash,state,error,created_at,updated_at) VALUES(?,?,?,?,?,?,'QUEUED',NULL,?,?)",
                         (uid(), episode["project_id"], episode_id, scene_key or "", digest({"input_hash": input_hash, "nonce": uid()}), input_hash, now(), now()))
        return self.state(episode_id)

    def process_next(self) -> dict | None:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM rough_cut_jobs WHERE state='QUEUED' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            job = dict(row)
            conn.execute("UPDATE rough_cut_jobs SET state='RUNNING',updated_at=? WHERE id=?", (now(), job["id"]))
        work = self.db.data_root / "roughcut-work" / job["operation_id"]
        try:
            with self.db.connect() as conn:
                episode = self.breakdown._episode(conn, job["episode_id"])
                state = self._state(conn, episode)
                shots, input_hash = self._scope(state, job["scene_key"] or None)
                if input_hash != job["input_hash"]:
                    raise DomainError("Shot đã đổi sau khi xếp việc ghép; ghép lại.", 409)
                timing = self.timing._state(conn, episode)
            root, fps, size = state["_root"], state["fps"], state["size"]
            wanted = {shot["shot_id"] for shot in shots}
            segments = []
            for shot in shots:
                source = root / shot["source"]["relative_path"]
                if not source.is_file() or file_sha256(source) != shot["source"]["sha256"]:
                    raise DomainError(f"{shot['shot_id']}: file nguồn thiếu hoặc đã bị đổi ngoài app.", 409)
                segment = self._segment(root, episode["code"], shot, fps, size)
                if not segment.is_file():
                    partial = segment.with_name(segment.stem + ".part.mp4")
                    self.media.conform(source, partial, fps, shot["length_frames"], size["width"], size["height"])
                    os.replace(partial, segment)
                segments.append(segment)
            timed = [shot for shot in timing["_stored"]["shots"] if shot["shot_id"] in wanted]
            audio = None
            if any(shot["lines"] for shot in timed):
                audio = work / "dialogue.wav"
                self.timing._mix(root, audio, timed, fps, {take["id"]: take for take in timing["_inputs"]["selected"].values()})
            total = sum(shot["length_frames"] for shot in shots)
            relative = f"media/roughcut/{episode['code']}/{job['scene_key'] or 'ca-tap'}-{input_hash[:12]}.mp4"
            self.media.join(segments, audio, root / relative, work)
            info = self.media.probe_video(root / relative)
            if info["frames"] != total:
                raise DomainError(f"Rough cut có {info['frames']} frame, timing đã duyệt là {total} frame; không dùng bản này.", 409)
            with self.db.transaction() as conn:
                previous = conn.execute("SELECT relative_path FROM rough_cuts WHERE episode_id=? AND scene_key=?", (job["episode_id"], job["scene_key"])).fetchone()
                conn.execute("INSERT OR REPLACE INTO rough_cuts(id,project_id,episode_id,scene_key,input_hash,relative_path,sha256,bytes,frames,width,height,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                             (uid(), job["project_id"], job["episode_id"], job["scene_key"], input_hash, relative, file_sha256(root / relative), (root / relative).stat().st_size,
                              info["frames"], info["width"], info["height"], now()))
                conn.execute("UPDATE rough_cut_jobs SET state='COMPLETED',error=NULL,updated_at=? WHERE id=?", (now(), job["id"]))
                self.studio.audit(conn, job["project_id"], "rough_cut.built", job["episode_id"], {"scene": job["scene_key"], "frames": total})
            if previous and previous["relative_path"] != relative:
                (root / previous["relative_path"]).unlink(missing_ok=True)
        except Exception as exc:
            with self.db.transaction() as conn:
                conn.execute("UPDATE rough_cut_jobs SET state='BLOCKED',error=?,updated_at=? WHERE id=?",
                             (str(exc)[:500] if isinstance(exc, DomainError) else f"{type(exc).__name__}: không ghép được rough cut.", now(), job["id"]))
        finally:
            shutil.rmtree(work, ignore_errors=True)
        with self.db.connect() as conn:
            return dict(conn.execute("SELECT id,scene_key,state,error,updated_at FROM rough_cut_jobs WHERE id=?", (job["id"],)).fetchone())

    def rough_cut_file(self, rough_cut_id: str) -> Path:
        with self.db.connect() as conn:
            row = require(conn.execute("SELECT * FROM rough_cuts WHERE id=?", (rough_cut_id,)).fetchone(), "rough cut")
        path = Path(self.studio.project(row["project_id"])["root"]).resolve() / row["relative_path"]
        if not path.is_file() or path.stat().st_size != row["bytes"]:
            raise DomainError("File rough cut thiếu hoặc đã bị đổi ngoài app; ghép lại.", 409)
        return path

    # ---- Picture Lock and C7 ---------------------------------------------------------------------------------------

    def lock_scene(self, episode_id: str, scene_key: str, expected_hash: str, checks: dict) -> dict:
        if not isinstance(checks, dict) or set(checks) != set(CHECKS) or any(checks[key] is not True for key in CHECKS):
            raise DomainError("Xác nhận đủ bốn mục đã xem trên rough cut: nhịp, độ liền mạch, hướng nhìn, màu và style.")
        with self.db.transaction() as conn:
            episode = self.breakdown._episode(conn, episode_id)
            state = self._state(conn, episode)
            scene = require(next((s for s in state["scenes"] if s["scene_key"] == scene_key), None), "scene")
            if scene["input_hash"] != expected_hash:
                raise DomainError("Scene đã đổi sau khi bạn xem; tải lại.", 409)
            if not scene["rough_cut"] or not scene["rough_cut"]["current"]:
                raise DomainError("Ghép rough cut của scene theo đúng các shot hiện tại rồi xem trước khi khóa dựng.", 409)
            conn.execute("INSERT OR REPLACE INTO scene_picture_locks(episode_id,scene_key,project_id,lock_hash,rough_cut_sha256,checks_json,created_at) VALUES(?,?,?,?,?,?,?)",
                         (episode_id, scene_key, episode["project_id"], expected_hash, scene["rough_cut"]["sha256"], canonical(sorted(CHECKS)), now()))
            self.studio.audit(conn, episode["project_id"], "rough_cut.scene_locked", episode_id, {"scene": scene_key, "shots": len(scene["shots"])})
        return self.state(episode_id)

    def unlock_scene(self, episode_id: str, scene_key: str) -> dict:
        with self.db.transaction() as conn:
            episode = self.breakdown._episode(conn, episode_id)
            if not conn.execute("DELETE FROM scene_picture_locks WHERE episode_id=? AND scene_key=?", (episode_id, scene_key)).rowcount:
                raise DomainError("Scene này chưa khóa dựng.", 409)
            self.studio.audit(conn, episode["project_id"], "rough_cut.scene_unlocked", episode_id, {"scene": scene_key})
        return self.state(episode_id)

    def approve_c7(self, episode_id: str, expected_hash: str) -> dict:
        with self.db.transaction() as conn:
            episode = self.breakdown._episode(conn, episode_id)
            state = self._state(conn, episode)
            if not state["ready"] or state["approve_blockers"]:
                raise DomainError("Chưa khóa dựng được cả tập: " + " ".join(state["approve_blockers"] or state["blockers"]), 409)
            if state["lock_hash"] != expected_hash:
                raise DomainError("Rough cut đã đổi sau khi bạn xem; tải lại.", 409)
            if state["c7"] and state["c7"]["current"]:
                raise DomainError("Tập này đã khóa dựng.", 409)
            shots = [shot for scene in state["scenes"] for shot in scene["shots"]]
            workflow.record_checkpoint(self.studio, conn, episode["project_id"], "C7", episode_id, (state["c7"]["sequence"] if state["c7"] else 0) + 1, expected_hash,
                                       {"scenes": len(state["scenes"]), "shots": len(shots), "total_frames": state["whole"]["frames"], "fps": state["fps"],
                                        "sources": {kind: sum(s["source"]["kind"] == kind for s in shots) for kind in ("UE_PROXY", "AI_DRAFT")},
                                        "locked_shots": [s["shot_id"] for s in shots], "finals_created": False})
        return self.state(episode_id)
