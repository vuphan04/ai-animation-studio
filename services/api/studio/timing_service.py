"""Workflow V4.5 checkpoint C4: shot lengths, pauses and action cues fixed from the dialogue takes in use.

The dialogue-only mixdown is for judging rhythm by ear. Approving C4 is what allows an episode to be
built: before it, V4.5 projects create no UE sequence, bake no cache and send no video request.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import wave
from pathlib import Path

from . import retime_impact, workflow
from .domain import DomainError, canonical, digest, file_sha256
from .service import now, require, uid
from .timing_domain import edit_shot, propose, required_frames, to_frames, to_samples, to_seconds
from .dialogue_provider import inspect_wav


MAX_AUDITION_SECONDS = 3 * 3600


class TimingService:
    def __init__(self, studio, breakdown, routes, dialogue):
        self.studio = studio
        self.db = studio.db
        self.breakdown = breakdown
        self.routes = routes
        self.dialogue = dialogue

    @staticmethod
    def _revision(conn, episode_id: str):
        return conn.execute("SELECT * FROM episode_timing_revisions WHERE episode_id=? ORDER BY version DESC LIMIT 1", (episode_id,)).fetchone()

    def _inputs(self, conn, episode: dict) -> dict:
        """Everything a timing is measured against: routes (C3), the takes in use and the series frame rate."""
        plan = self.routes._plan(conn, episode)
        blockers = list(plan["blockers"])
        if not blockers and not (plan["c3"] and plan["c3"]["current"]):
            blockers.append("C3: phương thức và chi phí của tập chưa được chốt.")
        standards = conn.execute("SELECT fps_num,fps_den FROM series_standards WHERE project_id=?", (episode["project_id"],)).fetchone()
        fps = {"num": standards["fps_num"], "den": standards["fps_den"]} if standards else None
        if not fps:
            blockers.append("S1: chưa lưu chuẩn FPS của series.")
        context = self.dialogue._context(conn, episode) if plan["ready"] else {"lines": {}, "revision": None}
        waiting = [line_id for line_id, line in context["lines"].items() if not line["selected"]]
        if waiting:
            blockers.append(f"Còn {len(waiting)} câu thoại chưa có take được duyệt ({', '.join(waiting[:4])}{'…' if len(waiting) > 4 else ''}).")
        return {"plan": plan, "fps": fps, "context": context, "blockers": blockers, "waiting": waiting,
                "selected": {line_id: line["selected"] for line_id, line in context["lines"].items() if line["selected"]}}

    def _state(self, conn, episode: dict) -> dict:
        inputs = self._inputs(conn, episode)
        plan, fps, context = inputs["plan"], inputs["fps"], inputs["context"]
        revision = self._revision(conn, episode["id"])
        stored = json.loads(revision["content_json"]) if revision else None
        breakdown = context["revision"]
        content = json.loads(breakdown["content_json"]) if breakdown else {"scenes": []}
        stale = []
        if stored:
            if not breakdown or stored["breakdown_hash"] != breakdown["content_hash"]:
                stale.append("Breakdown đã đổi sau khi dựng timing.")
            if stored["fps"] != fps:
                stale.append("Chuẩn FPS của series đã đổi.")
            changed = [line["line_id"] for shot in stored["shots"] for line in shot["lines"]
                       if (inputs["selected"].get(line["line_id"]) or {}).get("id") != line["take_id"]]
            if changed:
                stale.append("Take đang dùng đã đổi: " + ", ".join(changed[:6]) + ("…" if len(changed) > 6 else "") + ".")
            if not stale and not inputs["waiting"] and fps and propose(content, episode["code"], inputs["selected"], fps, stored, breakdown["content_hash"]) != stored:
                stale.append("Có shot không còn đủ dài cho thoại hiện tại.")
        routes = {shot["shot_id"]: shot for scene in plan["scenes"] for shot in scene["shots"]}
        described = {f"{episode['code']}_{scene['scene_key']}_{shot['shot_key']}": shot for scene in content["scenes"] for shot in scene["shots"]}
        headings = {scene["scene_key"]: scene["heading"] for scene in content["scenes"]}
        scenes, cursor, lines = [], 0, 0
        for shot in stored["shots"] if stored else []:
            source, route = described.get(shot["shot_id"], {}), routes.get(shot["shot_id"], {})
            if not scenes or scenes[-1]["scene_key"] != shot["scene_key"]:
                scenes.append({"scene_key": shot["scene_key"], "heading": headings.get(shot["scene_key"], ""), "start_frame": cursor, "length_frames": 0, "shots": []})
            local, listed = 0, []
            for line in shot["lines"]:
                local += line["pause_before_frames"]
                spoken = context["lines"].get(line["line_id"], {})
                listed.append({"line_id": line["line_id"], "take_id": line["take_id"], "speaker": spoken.get("speaker", ""), "text": spoken.get("spoken_text", ""),
                               "voice_frames": line["voice_frames"], "pause_before_frames": line["pause_before_frames"], "start_frame": local})
                local += line["voice_frames"]
            needed = required_frames(shot)
            scenes[-1]["shots"].append({"shot_id": shot["shot_id"], "description": source.get("description", ""), "route": route.get("route"),
                                        "estimated_frames": to_frames(source["duration_seconds"], stored["fps"]) if source else None,
                                        "length_frames": shot["length_frames"], "seconds": to_seconds(shot["length_frames"], stored["fps"]),
                                        "required_frames": needed, "tail_frames": shot["length_frames"] - needed, "start_frame": cursor,
                                        "lines": listed, "cues": shot["cues"]})
            scenes[-1]["length_frames"] += shot["length_frames"]
            cursor += shot["length_frames"]
            lines += len(listed)
        timing_hash = revision["content_hash"] if revision else None
        auditions = {row["scene_key"]: {"id": row["id"], "scene_key": row["scene_key"], "created_at": row["created_at"], "current": row["timing_hash"] == timing_hash,
                                        "seconds": round(row["sample_count"] / row["sample_rate"], 2)}
                     for row in conn.execute("SELECT * FROM timing_auditions WHERE episode_id=?", (episode["id"],))}
        cost = self.routes.retimed_cost(plan, {shot["shot_id"]: to_seconds(shot["length_frames"], stored["fps"]) for shot in stored["shots"]}) if stored and plan["ready"] else {"video": [], "issues": []}
        approve = list(inputs["blockers"])
        if not stored:
            approve.append("Chưa dựng timing từ thoại.")
        elif stale:
            approve.append("Timing đã cũ; bấm cập nhật timing rồi nghe lại.")
        else:
            approve += cost["issues"]
            if lines and not (auditions.get("") and auditions[""]["current"]):
                approve.append("Chưa ghép bản nghe thử cả tập cho đúng timing này.")
        latest = self.routes._checkpoint(conn, episode, "C4")
        c4 = {"exact_version": latest["exact_version"], "created_at": latest["created_at"], "decision": json.loads(latest["decision_json"]),
              "current": bool(stored) and not stale and latest["dependency_hash"] == timing_hash} if latest else None
        synthetic = sum(1 for take in inputs["selected"].values() if take["audio_kind"] != "SPEECH")
        # A timing that replaces an approved one: what it does to the work already built on the old one.
        impact = None
        if latest and stored and not stale and plan["ready"] and latest["dependency_hash"] != timing_hash:
            approved = conn.execute("SELECT content_json FROM episode_timing_revisions WHERE episode_id=? AND content_hash=?", (episode["id"], latest["dependency_hash"])).fetchone()
            if approved:
                impact = retime_impact.impact(conn, episode, plan, json.loads(approved["content_json"]), stored)
        return {"episode": episode, "fps": fps, "ready": not inputs["blockers"], "blockers": inputs["blockers"],
                "revision": {"version": revision["version"], "source": revision["source"], "created_at": revision["created_at"], "content_hash": timing_hash} if revision else None,
                "stale": bool(stale), "stale_reasons": stale, "scenes": scenes, "auditions": auditions, "cost": cost,
                "summary": {"shots": len(stored["shots"]) if stored else 0, "lines": lines, "total_frames": cursor,
                            "duration_seconds": to_seconds(cursor, stored["fps"]) if stored else 0,
                            "estimated_seconds": round(sum(shot["duration_seconds"] for shot in described.values()), 2), "synthetic_takes": synthetic},
                "approve_blockers": approve, "allowed": not approve, "c4": c4, "impact": impact, "_stored": stored, "_inputs": inputs}

    @staticmethod
    def _public(state: dict) -> dict:
        return {key: value for key, value in state.items() if not key.startswith("_")}

    def state(self, episode_id: str) -> dict:
        with self.db.connect() as conn:
            return self._public(self._state(conn, self.breakdown._episode(conn, episode_id)))

    # ---- drafting --------------------------------------------------------------------------------------------------

    def _write(self, conn, episode: dict, content: dict, source: str, current) -> None:
        content_hash = digest(content)
        if current and current["content_hash"] == content_hash:
            raise DomainError("Timing không thay đổi; không tạo phiên bản mới.", 409)
        version = (current["version"] if current else 0) + 1
        conn.execute("INSERT INTO episode_timing_revisions(id,project_id,episode_id,version,source,content_json,content_hash,created_at) VALUES(?,?,?,?,?,?,?,?)",
                     (uid(), episode["project_id"], episode["id"], version, source, canonical(content), content_hash, now()))
        self.studio.audit(conn, episode["project_id"], "timing.revision", episode["id"], {"version": version, "source": source})

    def propose(self, episode_id: str, expected_hash: str | None) -> dict:
        """Build the timing from the takes in use, or bring an existing one up to date without losing manual edits."""
        with self.db.transaction() as conn:
            episode = self.breakdown._episode(conn, episode_id)
            inputs = self._inputs(conn, episode)
            if inputs["blockers"]:
                raise DomainError("Chưa dựng được timing: " + " ".join(inputs["blockers"]), 409)
            current = self._revision(conn, episode_id)
            if (current["content_hash"] if current else None) != expected_hash:
                raise DomainError("Timing đã đổi ở phiên khác; tải lại.", 409)
            breakdown = inputs["context"]["revision"]
            content = propose(json.loads(breakdown["content_json"]), episode["code"], inputs["selected"], inputs["fps"],
                              json.loads(current["content_json"]) if current else None, breakdown["content_hash"])
            self._write(conn, episode, content, "SYNCED" if current else "PROPOSED", current)
        return self.state(episode_id)

    def save_shot(self, episode_id: str, expected_hash: str, shot_id: str, length_frames: int, pauses: dict, cues: list) -> dict:
        with self.db.transaction() as conn:
            episode = self.breakdown._episode(conn, episode_id)
            state = self._state(conn, episode)
            current = self._revision(conn, episode_id)
            if not current or current["content_hash"] != expected_hash:
                raise DomainError("Timing đã đổi ở phiên khác; tải lại.", 409)
            if state["stale"] or state["blockers"]:
                raise DomainError("Cập nhật timing theo thoại hiện tại trước khi chỉnh từng shot.", 409)
            stored = state["_stored"]
            shot = require(next((s for s in stored["shots"] if s["shot_id"] == shot_id), None), "shot")
            edited = edit_shot(shot, length_frames, pauses, cues, stored["fps"])
            self._write(conn, episode, dict(stored, shots=[edited if s["shot_id"] == shot_id else s for s in stored["shots"]]), "MANUAL_EDIT", current)
        return self.state(episode_id)

    # ---- dialogue-only audition ------------------------------------------------------------------------------------

    def _mix(self, root: Path, target: Path, shots: list[dict], fps: dict, takes: dict) -> tuple[int, int]:
        rates = sorted({line["sample_rate"] for shot in shots for line in shot["lines"]})
        if len(rates) > 1:
            raise DomainError(f"Các take đang dùng có sample rate khác nhau ({', '.join(map(str, rates))} Hz); tạo lại take cho thống nhất trước khi ghép bản nghe thử.", 409)
        rate = rates[0] if rates else 24000
        total = to_samples(sum(shot["length_frames"] for shot in shots), rate, fps)
        if total > rate * MAX_AUDITION_SECONDS:
            raise DomainError("Bản nghe thử dài quá 3 giờ; ghép theo từng scene.", 409)
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_suffix(".part")
        written = 0
        with wave.open(str(part), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(rate)

            def silence(until: int) -> None:
                nonlocal written
                while written < until:
                    chunk = min(until - written, rate)
                    out.writeframesraw(b"\x00\x00" * chunk)
                    written += chunk

            start = 0
            for shot in shots:
                cursor = start
                for line in shot["lines"]:
                    cursor += line["pause_before_frames"]
                    silence(to_samples(cursor, rate, fps))
                    data = (root / takes[line["take_id"]]["relative_path"]).read_bytes()
                    if hashlib.sha256(data).hexdigest() != line["sha256"] or inspect_wav(data) != (line["sample_rate"], line["sample_count"]):
                        raise DomainError(f"{line['line_id']}: file thoại thiếu hoặc đã bị đổi ngoài app.", 409)
                    with wave.open(io.BytesIO(data), "rb") as take:
                        out.writeframesraw(take.readframes(line["sample_count"]))
                    written += line["sample_count"]
                    cursor += line["voice_frames"]
                start += shot["length_frames"]
            silence(total)
        os.replace(part, target)
        return rate, written

    def build_audition(self, episode_id: str, expected_hash: str, scene_key: str | None) -> dict:
        with self.db.connect() as conn:
            episode = self.breakdown._episode(conn, episode_id)
            state = self._state(conn, episode)
        revision, stored = state["revision"], state["_stored"]
        if not revision or revision["content_hash"] != expected_hash:
            raise DomainError("Timing đã đổi sau khi bạn xem; tải lại.", 409)
        if state["stale"] or state["blockers"]:
            raise DomainError("Cập nhật timing theo thoại hiện tại trước khi ghép bản nghe thử.", 409)
        shots = [shot for shot in stored["shots"] if not scene_key or shot["scene_key"] == scene_key]
        if not shots:
            raise DomainError("Scene này không có trong timing hiện tại.", 409)
        takes = {take["id"]: take for take in state["_inputs"]["selected"].values()}
        root = Path(self.studio.project(episode["project_id"])["root"]).resolve()
        relative = f"audio/audition/{episode['code']}-{scene_key or 'ca-tap'}-{expected_hash[:12]}.wav"
        try:
            rate, count = self._mix(root, root / relative, shots, stored["fps"], takes)
        except OSError as exc:
            raise DomainError("Không ghi được bản nghe thử vào thư mục dự án.", 409) from exc
        finally:
            # A mixdown that stopped half-way leaves only its own temporary file behind.
            (root / relative).with_suffix(".part").unlink(missing_ok=True)
        with self.db.transaction() as conn:
            previous = conn.execute("SELECT relative_path FROM timing_auditions WHERE episode_id=? AND scene_key=?", (episode_id, scene_key or "")).fetchone()
            conn.execute("INSERT OR REPLACE INTO timing_auditions(id,project_id,episode_id,timing_hash,scene_key,relative_path,sha256,bytes,sample_rate,sample_count,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (uid(), episode["project_id"], episode_id, expected_hash, scene_key or "", relative, file_sha256(root / relative), (root / relative).stat().st_size, rate, count, now()))
            self.studio.audit(conn, episode["project_id"], "timing.audition_built", episode_id, {"scene": scene_key, "samples": count})
        if previous and previous["relative_path"] != relative:
            # The superseded mixdown is derived from data the app still holds; only the current one is kept.
            (root / previous["relative_path"]).unlink(missing_ok=True)
        return self.state(episode_id)

    def audition_file(self, audition_id: str) -> Path:
        with self.db.connect() as conn:
            row = require(conn.execute("SELECT * FROM timing_auditions WHERE id=?", (audition_id,)).fetchone(), "audition")
        path = Path(self.studio.project(row["project_id"])["root"]).resolve() / row["relative_path"]
        if not path.is_file() or path.stat().st_size != row["bytes"]:
            raise DomainError("Bản nghe thử thiếu hoặc đã bị đổi ngoài app; ghép lại.", 409)
        return path

    # ---- C4 --------------------------------------------------------------------------------------------------------

    def approve_c4(self, episode_id: str, expected_hash: str, impact_hash: str | None = None) -> dict:
        """`impact_hash` is the operator's agreement to the listed rework when this timing replaces an approved one."""
        with self.db.transaction() as conn:
            episode = self.breakdown._episode(conn, episode_id)
            state = self._state(conn, episode)
            if state["approve_blockers"]:
                raise DomainError("Chưa duyệt được C4: " + " · ".join(state["approve_blockers"][:6]), 409)
            revision = state["revision"]
            if revision["content_hash"] != expected_hash:
                raise DomainError("Timing đã đổi sau khi bạn nghe; tải lại.", 409)
            if state["c4"] and state["c4"]["current"]:
                raise DomainError("Timing này đã được duyệt.", 409)
            impact = state["impact"]
            if impact and impact["requires_ack"] and impact_hash != impact["hash"]:
                raise DomainError("Timing mới làm cũ phần đã dựng; xem danh sách shot bị ảnh hưởng và chi phí tạo lại rồi xác nhận cập nhật.", 409)
            summary = state["summary"]
            workflow.record_checkpoint(self.studio, conn, episode["project_id"], "C4", episode_id, revision["version"], expected_hash,
                                       {"shots": summary["shots"], "lines": summary["lines"], "total_frames": summary["total_frames"],
                                        "duration_seconds": summary["duration_seconds"], "fps": state["fps"],
                                        "synthetic_takes": summary["synthetic_takes"], "breakdown_hash": state["_stored"]["breakdown_hash"],
                                        "retime": {"shots": [shot["shot_id"] for shot in impact["shots"]], "removed": impact["removed"], "scenes_reopened": len(impact["scenes"]),
                                                   "totals": impact["totals"]} if impact else None})
        return self.state(episode_id)
