"""Workflow V4.5 step 06: dialogue takes for every line of the approved breakdown.

A take is one generated reading of one line. The operator listens and approves the take to use; the
measured sample count of that take is what shot timing is built from. Subtitles keep the breakdown text.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

from . import budget
from .breakdown_domain import entity_index, resolve
from .dialogue_provider import DialogueAudio, DialogueNotSent, DialogueOutcomeUnknown, ElevenLabsDialogueProvider, FixtureDialogueProvider, inspect_wav
from .domain import DomainError, canonical, digest, file_sha256
from .service import now, require, uid
from .voice_settings import OUTPUT_FORMATS, PROVIDER, VoiceSettings


LANGUAGES = ("ORIGINAL", "EN")
SETTING_RANGES = {"stability": (0.0, 1.0), "similarity_boost": (0.0, 1.0), "speed": (0.7, 1.2)}
ACTIVE = ("QUEUED", "RUNNING", "RECONCILING")


class DialogueService:
    def __init__(self, studio, breakdown, routes, *, provider=None, voice_settings=None):
        self.studio = studio
        self.db = studio.db
        self.breakdown = breakdown
        self.routes = routes
        self.custom_provider = provider
        self.voice_settings = voice_settings or VoiceSettings(studio)
        with self.db.transaction() as conn:
            # A paid request interrupted by a restart has an unknown outcome and is never resent.
            conn.execute("UPDATE dialogue_jobs SET state=CASE WHEN provider_kind=? THEN 'RECONCILING' ELSE 'BLOCKED' END,error='Backend khởi động lại trong lúc tạo thoại.',updated_at=? WHERE state='RUNNING'", (PROVIDER, now()))

    def _provider(self) -> tuple[object, dict, list[str]]:
        settings = self.voice_settings.get()
        if self.custom_provider is not None:
            return self.custom_provider, settings, []
        if settings["provider"] != PROVIDER:
            return FixtureDialogueProvider(), settings, []
        blockers = []
        if not settings["key_configured"]:
            blockers.append("Chưa có khóa ElevenLabs trong kho bảo mật Windows.")
        if not settings["model_id"]:
            blockers.append("Chưa chọn model tạo thoại ở Cài đặt → Giọng thoại.")
        return ElevenLabsDialogueProvider(), settings, blockers

    # ---- lines -----------------------------------------------------------------------------------------------------

    def _context(self, conn, episode: dict) -> dict:
        """Every dialogue line of the current breakdown with its voice, the text to read and its takes."""
        revision = self.breakdown._revision(conn, episode["id"])
        c1 = self.breakdown._c1(conn, episode, revision)
        blockers = []
        if not revision:
            blockers.append("Chưa có breakdown của tập.")
        elif not c1 or not c1["current"]:
            blockers.append("C1: breakdown hiện tại chưa được chốt.")
        setting = conn.execute("SELECT language,revision FROM episode_voice_settings WHERE episode_id=?", (episode["id"],)).fetchone()
        language = {"value": setting["language"] if setting else "ORIGINAL", "revision": setting["revision"] if setting else 0}
        if blockers:
            return {"blockers": blockers, "language": language, "scenes": [], "lines": {}, "revision": revision}
        bible = conn.execute("SELECT snapshot_json FROM style_bible_versions WHERE project_id=? ORDER BY version DESC LIMIT 1", (episode["project_id"],)).fetchone()
        voices = {e["entity_key"]: e["voice"] for e in json.loads(bible["snapshot_json"])["entities"] if e["kind"] == "character"} if bible else {}
        index = entity_index(self.breakdown._entities(conn, episode["project_id"]))
        rows = {r["line_id"]: r for r in conn.execute("SELECT * FROM dialogue_lines WHERE episode_id=?", (episode["id"],))}
        takes: dict[str, list] = {}
        for take in conn.execute("SELECT * FROM dialogue_takes WHERE episode_id=? ORDER BY version DESC", (episode["id"],)):
            takes.setdefault(take["line_id"], []).append(dict(take))
        jobs = {r["line_id"]: r for r in conn.execute("SELECT * FROM dialogue_jobs WHERE episode_id=? AND state IN ('QUEUED','RUNNING','RECONCILING') ORDER BY created_at", (episode["id"],))}
        failed = {}
        for job in conn.execute("SELECT line_id,state,error FROM dialogue_jobs WHERE episode_id=? ORDER BY created_at", (episode["id"],)):
            failed[job["line_id"]] = job["error"] if job["state"] == "BLOCKED" else None
        root = Path(self.studio.project(episode["project_id"])["root"]).resolve()
        scenes, lines = [], {}
        for scene in json.loads(revision["content_json"])["scenes"]:
            scene_lines = []
            for shot in scene["shots"]:
                for line in shot["dialogue"]:
                    line_id = f"{episode['code']}_{scene['scene_key']}_{line['line_key']}"
                    speaker = resolve(index, "character", line["character"]["name"], line["character"]["entity_key"])
                    voice = voices.get(speaker) or {"mode": "UNSET", "voice_id": None, "voice_name": None}
                    row = rows.get(line_id)
                    override = row["spoken_text"] if row and row["spoken_text"] else None
                    spoken = (override or (line["text_en"] if language["value"] == "EN" else line["text"])).strip()
                    issues = []
                    if not speaker:
                        issues.append("Người nói chưa có trong thư viện series.")
                    elif voice["mode"] != "ELEVENLABS" or not voice["voice_id"]:
                        issues.append("Nhân vật chưa được gán giọng trong Style bible đã duyệt; gán giọng ở S4 rồi duyệt lại C2.")
                    if not spoken:
                        issues.append("Thiếu bản dịch tiếng Anh của câu này; bổ sung ở C1 hoặc nhập lời đọc.")
                    direction = digest({"text": spoken, "voice_id": voice["voice_id"]})
                    selected_id = row["selected_take_id"] if row else None
                    listed, selected = [], None
                    for take in takes.get(line_id, []):
                        current = take["text_hash"] == direction
                        item = {"id": take["id"], "version": take["version"], "review": take["review"], "provider_kind": take["provider_kind"],
                                "audio_kind": take["audio_kind"], "model_id": take["model_id"], "sample_rate": take["sample_rate"],
                                "sample_count": take["sample_count"], "seconds": round(take["sample_count"] / take["sample_rate"], 3),
                                "text": take["text"], "created_at": take["created_at"], "current": current,
                                "file_ok": (root / take["relative_path"]).is_file(),
                                "selected": take["id"] == selected_id and current and take["review"] == "APPROVED"}
                        if item["selected"] and item["file_ok"]:
                            selected = take
                        listed.append(item)
                    job = jobs.get(line_id)
                    status = ("BLOCKED" if issues else "WORKING" if job and job["state"] != "RECONCILING" else "READY" if selected
                              else "NEEDS_REVIEW" if any(t["current"] and t["review"] == "NEEDS_REVIEW" for t in listed) else "NEEDS_TAKE")
                    item = {"line_id": line_id, "scene_key": scene["scene_key"], "shot_id": f"{episode['code']}_{scene['scene_key']}_{shot['shot_key']}",
                            "speaker": line["character"]["name"], "speaker_key": speaker, "voice_id": voice["voice_id"], "voice_name": voice["voice_name"],
                            "text": line["text"], "text_en": line["text_en"], "spoken_text": spoken, "spoken_override": override,
                            "settings": json.loads(row["settings_json"]) if row else {}, "revision": row["revision"] if row else 0,
                            "direction_hash": direction, "issues": issues, "status": status, "takes": listed,
                            "job": {"id": job["id"], "state": job["state"], "error": job["error"]} if job else None,
                            "last_error": failed.get(line_id), "selected": selected}
                    scene_lines.append(item)
                    lines[line_id] = item
            scenes.append({"scene_key": scene["scene_key"], "heading": scene["heading"], "lines": scene_lines})
        return {"blockers": [], "language": language, "scenes": scenes, "lines": lines, "revision": revision}

    def _estimate(self, conn, project_id: str, characters: int) -> tuple[int, str]:
        voice = self.routes._settings(conn, project_id)["prices"]["voice"]
        return (characters * voice["micro_per_1k_chars"] + 999) // 1000, voice["currency"]

    def state(self, episode_id: str) -> dict:
        provider, settings, provider_blockers = self._provider()
        with self.db.connect() as conn:
            episode = self.breakdown._episode(conn, episode_id)
            context = self._context(conn, episode)
            c3 = self.routes.c3_current(conn, episode)
            lines = list(context["lines"].values())
            waiting = [line for line in lines if line["status"] == "NEEDS_TAKE"]
            chars = sum(len(line["spoken_text"]) for line in waiting)
            amount, currency = self._estimate(conn, episode["project_id"], chars)
            gate = budget.check(conn, episode["project_id"], currency, amount) if provider.paid else {"allowed": True, "enforced": False, "reason": None}
            jobs = [dict(r) for r in conn.execute("SELECT id,line_id,provider_kind,state,estimated_micro,actual_micro,currency,error,updated_at FROM dialogue_jobs WHERE episode_id=? ORDER BY created_at DESC LIMIT 8", (episode_id,))]
        blockers = list(context["blockers"])
        if not blockers and not c3:
            blockers.append("C3: phương thức và chi phí của tập chưa được chốt.")
        counts = {status: sum(1 for line in lines if line["status"] == status) for status in ("READY", "NEEDS_REVIEW", "NEEDS_TAKE", "WORKING", "BLOCKED")}
        scenes = [dict(scene, lines=[{k: v for k, v in line.items() if k != "selected"} for line in scene["lines"]]) for scene in context["scenes"]]
        return {"episode": episode, "ready": not blockers, "blockers": blockers, "language": context["language"], "scenes": scenes,
                "summary": {"lines": len(lines), **counts},
                "provider": {"kind": provider.kind, "paid": provider.paid, "model_id": settings["model_id"], "output_format": settings["output_format"],
                             "blockers": provider_blockers,
                             "note": None if provider.paid else "Chưa chọn ElevenLabs: take là âm thử tổng hợp, không phải giọng nói; chỉ dùng để thử luồng."},
                "pending": {"lines": len(waiting), "characters": chars, "estimated_micro": amount, "currency": currency, "budget": gate},
                "jobs": jobs, "setting_ranges": SETTING_RANGES}

    def selected_takes(self, conn, episode: dict) -> tuple[dict, dict]:
        """For timing: the take in use for every line, and the lines that have none yet."""
        context = self._context(conn, episode)
        if context["blockers"]:
            raise DomainError("Chưa có thoại để dựng timing: " + " ".join(context["blockers"]), 409)
        return ({line_id: line["selected"] for line_id, line in context["lines"].items() if line["selected"]},
                {line_id: line for line_id, line in context["lines"].items() if not line["selected"]})

    def save_language(self, episode_id: str, expected_revision: int, language: str) -> dict:
        if language not in LANGUAGES:
            raise DomainError("Chọn đọc thoại theo bản gốc hoặc bản dịch tiếng Anh.")
        with self.db.transaction() as conn:
            episode = self.breakdown._episode(conn, episode_id)
            row = conn.execute("SELECT revision FROM episode_voice_settings WHERE episode_id=?", (episode_id,)).fetchone()
            if (row["revision"] if row else 0) != expected_revision:
                raise DomainError("Ngôn ngữ thoại đã đổi ở phiên khác; tải lại.", 409)
            conn.execute("INSERT OR REPLACE INTO episode_voice_settings(episode_id,project_id,language,revision,updated_at) VALUES(?,?,?,?,?)",
                         (episode_id, episode["project_id"], language, expected_revision + 1, now()))
            self.studio.audit(conn, episode["project_id"], "dialogue.language_saved", episode_id, {"language": language})
        return self.state(episode_id)

    def save_line(self, episode_id: str, line_id: str, expected_revision: int, spoken_text: str | None, settings: dict) -> dict:
        if spoken_text is not None and (not isinstance(spoken_text, str) or len(spoken_text) > 2000):
            raise DomainError("Lời đọc tối đa 2000 ký tự.")
        if not isinstance(settings, dict) or any(key not in SETTING_RANGES or type(value) not in (int, float) or not SETTING_RANGES[key][0] <= value <= SETTING_RANGES[key][1]
                                                 for key, value in settings.items()):
            raise DomainError("Thông số giọng không hợp lệ: ổn định và độ giống từ 0 đến 1, tốc độ từ 0,7 đến 1,2.")
        with self.db.transaction() as conn:
            episode = self.breakdown._episode(conn, episode_id)
            line = require(self._context(conn, episode)["lines"].get(line_id), "dialogue line")
            if line["revision"] != expected_revision:
                raise DomainError("Câu thoại này đã đổi ở phiên khác; tải lại.", 409)
            conn.execute("INSERT INTO dialogue_lines(episode_id,line_id,project_id,spoken_text,settings_json,selected_take_id,revision,updated_at) VALUES(?,?,?,?,?,NULL,1,?) "
                         "ON CONFLICT(episode_id,line_id) DO UPDATE SET spoken_text=excluded.spoken_text,settings_json=excluded.settings_json,revision=revision+1,updated_at=excluded.updated_at",
                         (episode_id, line_id, episode["project_id"], (spoken_text or "").strip() or None, canonical({k: float(v) for k, v in settings.items()}), now()))
            self.studio.audit(conn, episode["project_id"], "dialogue.line_saved", episode_id, {"line": line_id})
        return self.state(episode_id)

    # ---- generation ------------------------------------------------------------------------------------------------

    def enqueue(self, episode_id: str, line_ids: list[str] | None, client_request_id: str) -> dict:
        """Queue one take per requested line; with no list, every line that has no usable take yet."""
        try:
            uuid.UUID(client_request_id)
        except (ValueError, AttributeError) as exc:
            raise DomainError("client_request_id phải là UUID.") from exc
        provider, settings, provider_blockers = self._provider()
        if provider_blockers:
            raise DomainError("Chưa tạo được thoại: " + " ".join(provider_blockers), 409)
        queued, skipped = 0, []
        with self.db.transaction() as conn:
            episode = self.breakdown._episode(conn, episode_id)
            context = self._context(conn, episode)
            if context["blockers"]:
                raise DomainError("Chưa tạo được thoại: " + " ".join(context["blockers"]), 409)
            if not self.routes.c3_current(conn, episode):
                raise DomainError("C3: phương thức và chi phí của tập chưa được chốt; tạo thoại sau khi qua cổng ngân sách 1.", 409)
            if line_ids is None:
                targets = [line for line in context["lines"].values() if line["status"] == "NEEDS_TAKE"]
            else:
                if not line_ids or len(set(line_ids)) != len(line_ids) or any(line_id not in context["lines"] for line_id in line_ids):
                    raise DomainError("Danh sách câu thoại không hợp lệ hoặc không thuộc breakdown hiện tại.", 409)
                targets = [context["lines"][line_id] for line_id in line_ids]
            for line in targets:
                if line["issues"] or line["job"]:
                    reason = line["issues"][0] if line["issues"] else ("Lần tạo trước chưa rõ kết quả; đóng khoản giữ trong Sổ chi phí trước." if line["job"]["state"] == "RECONCILING" else "Đang có việc tạo thoại cho câu này.")
                    if line_ids is not None and (line["issues"] or line["job"]["state"] == "RECONCILING"):
                        raise DomainError(f"{line['line_id']}: {reason}", 409)
                    skipped.append({"line_id": line["line_id"], "reason": reason})
                    continue
                request = {"schema_version": "dialogue.request.1", "episode_id": episode_id, "line_id": line["line_id"], "text": line["spoken_text"],
                           "direction_hash": line["direction_hash"], "voice_id": line["voice_id"], "voice_name": line["voice_name"],
                           "provider_kind": provider.kind, "model_id": settings["model_id"] if provider.kind == PROVIDER else "",
                           "output_format": settings["output_format"], "sample_rate": OUTPUT_FORMATS[settings["output_format"]], "settings": line["settings"]}
                input_hash = digest(request)
                operation_id = digest({"input_hash": input_hash, "client_request_id": client_request_id})
                if conn.execute("SELECT 1 FROM dialogue_jobs WHERE operation_id=?", (operation_id,)).fetchone():
                    continue
                estimate, currency = self._estimate(conn, episode["project_id"], len(line["spoken_text"]))
                if provider.paid:
                    budget.reserve(conn, episode["project_id"], operation_id, gate=1, category="VOICE", provider_kind=provider.kind,
                                   currency=currency, amount_micro=estimate)
                request["operation_id"] = operation_id
                stamp = now()
                conn.execute("INSERT INTO dialogue_jobs(id,project_id,episode_id,line_id,provider_kind,operation_id,input_hash,request_json,state,estimated_micro,actual_micro,currency,provider_request_id,take_id,error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,'QUEUED',?,NULL,?,NULL,NULL,NULL,?,?)",
                             (uid(), episode["project_id"], episode_id, line["line_id"], provider.kind, operation_id, input_hash, canonical(request), estimate, currency, stamp, stamp))
                queued += 1
            self.studio.audit(conn, episode["project_id"], "dialogue.queued", episode_id, {"provider": provider.kind, "lines": queued})
        return {**self.state(episode_id), "queued": queued, "skipped": skipped}

    def _finish(self, job: dict, state: str, error: str | None, *, billed: bool = False, release: bool = False,
                take_id: str | None = None, request_id: str | None = None) -> dict:
        with self.db.transaction() as conn:
            conn.execute("UPDATE dialogue_jobs SET state=?,error=?,actual_micro=CASE WHEN ? THEN estimated_micro ELSE actual_micro END,take_id=COALESCE(?,take_id),provider_request_id=COALESCE(?,provider_request_id),updated_at=? WHERE id=?",
                         (state, error, int(billed), take_id, request_id, now(), job["id"]))
            if billed:
                # ElevenLabs bills by the characters sent, so the reserved amount is what the request cost.
                budget.settle(conn, job["operation_id"], job["estimated_micro"])
            elif release:
                budget.release(conn, job["operation_id"])
            self.studio.audit(conn, job["project_id"], "dialogue." + state.lower(), job["id"], {"line": job["line_id"]})
            return dict(conn.execute("SELECT * FROM dialogue_jobs WHERE id=?", (job["id"],)).fetchone())

    def _save_take(self, job: dict, request: dict, audio: DialogueAudio) -> str:
        if inspect_wav(audio.wav) != (audio.sample_rate, audio.sample_count) or audio.sample_count <= 0:
            raise DomainError("Âm thanh trả về không khớp số mẫu đã báo; không lưu take.", 409)
        episode_code, scene_key, line_key = job["line_id"].split("_", 2)
        root = Path(self.studio.project(job["project_id"])["root"]).resolve()
        with self.db.transaction() as conn:
            version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM dialogue_takes WHERE episode_id=? AND line_id=?", (job["episode_id"], job["line_id"])).fetchone()[0]
            relative = f"audio/dialogue/{episode_code}/{scene_key}/{line_key}-v{version}.wav"
            if (root / relative).exists():
                # Never overwrite a file the app did not just create; paid audio still has to land somewhere.
                relative = f"audio/dialogue/{episode_code}/{scene_key}/{line_key}-v{version}-{job['operation_id'][:8]}.wav"
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            part = target.with_suffix(".part")
            part.write_bytes(audio.wav)
            os.replace(part, target)
            take_id = uid()
            conn.execute("INSERT INTO dialogue_takes(id,project_id,episode_id,line_id,version,job_id,provider_kind,audio_kind,voice_id,model_id,text,text_hash,settings_json,relative_path,sha256,bytes,sample_rate,sample_count,provider_request_id,review,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'NEEDS_REVIEW',?)",
                         (take_id, job["project_id"], job["episode_id"], job["line_id"], version, job["id"], job["provider_kind"], audio.audio_kind,
                          request["voice_id"], request["model_id"], request["text"], request["direction_hash"], canonical(request["settings"]), relative,
                          hashlib.sha256(audio.wav).hexdigest(), len(audio.wav), audio.sample_rate, audio.sample_count, audio.provider_request_id, now()))
        return take_id

    def process_next(self) -> dict | None:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM dialogue_jobs WHERE state='QUEUED' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            job = dict(row)
            conn.execute("UPDATE dialogue_jobs SET state='RUNNING',updated_at=? WHERE id=?", (now(), job["id"]))
        request = json.loads(job["request_json"])
        provider = self.custom_provider if self.custom_provider is not None else (ElevenLabsDialogueProvider() if job["provider_kind"] == PROVIDER else FixtureDialogueProvider())
        sent = False
        try:
            with self.db.connect() as conn:
                line = self._context(conn, self.breakdown._episode(conn, job["episode_id"]))["lines"].get(job["line_id"])
            if not line or line["direction_hash"] != request["direction_hash"]:
                raise DialogueNotSent("Lời đọc, giọng hoặc breakdown đã đổi sau khi xếp việc; yêu cầu chưa gửi.", 409)
            sent = provider.paid
            audio = provider.render(request)
        except DialogueNotSent as exc:
            return self._finish(job, "BLOCKED", str(exc)[:500], release=True)
        except DialogueOutcomeUnknown as exc:
            return self._finish(job, "RECONCILING", str(exc)[:500])
        except DomainError as exc:
            # The provider answered 200 with unusable audio: billed, nothing to keep.
            return self._finish(job, "BLOCKED", str(exc)[:500], billed=sent, release=not sent)
        except Exception as exc:
            return self._finish(job, "RECONCILING" if sent else "BLOCKED", f"{type(exc).__name__}: không rõ kết quả tạo thoại." if sent else f"{type(exc).__name__}: không tạo được thoại.", release=not sent)
        try:
            take_id = self._save_take(job, request, audio)
        except (DomainError, OSError) as exc:
            return self._finish(job, "BLOCKED", "Đã nhận âm thanh nhưng không lưu được take: " + str(exc)[:300], billed=provider.paid, request_id=audio.provider_request_id)
        return self._finish(job, "COMPLETED", None, billed=provider.paid, take_id=take_id, request_id=audio.provider_request_id)

    # ---- review ----------------------------------------------------------------------------------------------------

    def _take(self, conn, take_id: str) -> dict:
        return dict(require(conn.execute("SELECT * FROM dialogue_takes WHERE id=?", (take_id,)).fetchone(), "dialogue take"))

    def take_file(self, take_id: str) -> Path:
        with self.db.connect() as conn:
            take = self._take(conn, take_id)
        path = Path(self.studio.project(take["project_id"])["root"]).resolve() / take["relative_path"]
        if not path.is_file() or file_sha256(path) != take["sha256"]:
            raise DomainError("File thoại thiếu hoặc đã bị đổi ngoài app.", 409)
        return path

    def _select(self, conn, take: dict) -> None:
        conn.execute("UPDATE dialogue_takes SET review='APPROVED' WHERE id=?", (take["id"],))
        conn.execute("INSERT INTO dialogue_lines(episode_id,line_id,project_id,spoken_text,settings_json,selected_take_id,revision,updated_at) VALUES(?,?,?,NULL,'{}',?,1,?) "
                     "ON CONFLICT(episode_id,line_id) DO UPDATE SET selected_take_id=excluded.selected_take_id,revision=revision+1,updated_at=excluded.updated_at",
                     (take["episode_id"], take["line_id"], take["project_id"], take["id"], now()))

    def review_take(self, take_id: str, decision: str) -> dict:
        """APPROVED makes this the take in use for its line; REJECTED also removes it from use."""
        if decision not in ("APPROVED", "REJECTED"):
            raise DomainError("Quyết định duyệt take không hợp lệ.")
        if decision == "APPROVED":
            self.take_file(take_id)  # The decision must be about the exact audio that was generated.
        with self.db.transaction() as conn:
            take = self._take(conn, take_id)
            if decision == "APPROVED":
                line = self._context(conn, self.breakdown._episode(conn, take["episode_id"]))["lines"].get(take["line_id"])
                if not line or line["direction_hash"] != take["text_hash"]:
                    raise DomainError("Take này được tạo từ lời đọc hoặc giọng cũ; tạo take mới rồi duyệt.", 409)
                self._select(conn, take)
            else:
                conn.execute("UPDATE dialogue_takes SET review='REJECTED' WHERE id=?", (take_id,))
                conn.execute("UPDATE dialogue_lines SET selected_take_id=NULL,revision=revision+1,updated_at=? WHERE episode_id=? AND line_id=? AND selected_take_id=?",
                             (now(), take["episode_id"], take["line_id"], take_id))
            self.studio.audit(conn, take["project_id"], "dialogue.take_reviewed", take_id, {"line": take["line_id"], "decision": decision})
        return self.state(take["episode_id"])

    def approve_scene(self, episode_id: str, scene_key: str) -> dict:
        """Put the newest unreviewed take of every line in the scene into use, after the operator has listened."""
        approved = 0
        with self.db.transaction() as conn:
            episode = self.breakdown._episode(conn, episode_id)
            scene = require(next((s for s in self._context(conn, episode)["scenes"] if s["scene_key"] == scene_key), None), "scene")
            root = Path(self.studio.project(episode["project_id"])["root"]).resolve()
            for line in scene["lines"]:
                candidate = next((t for t in line["takes"] if t["current"] and t["review"] == "NEEDS_REVIEW"), None)
                if line["selected"] or not candidate:
                    continue
                take = self._take(conn, candidate["id"])
                if file_sha256(root / take["relative_path"]) != take["sha256"]:
                    raise DomainError(f"{line['line_id']}: file thoại đã bị đổi ngoài app.", 409)
                self._select(conn, take)
                approved += 1
            self.studio.audit(conn, episode["project_id"], "dialogue.scene_approved", episode_id, {"scene": scene_key, "takes": approved})
        return {**self.state(episode_id), "approved": approved}
