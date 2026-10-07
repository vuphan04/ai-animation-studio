"""Workflow V4.5 step 13 and checkpoint C10: assembly, timeline package, automatic QC and the Resolve hand-off.

A package is one folder: the conformed master of every shot, the stems approved at C9, an FCPXML timeline
(with an EDL as picture-only fallback), SRT subtitles in Vietnamese and English, an MP4 preview and the QC
report. The app does not drive DaVinci Resolve: the operator imports the timeline, grades, mixes and
exports there, then registers the exported film here for C10.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from . import workflow
from .delivery_domain import edl, fcpxml, srt
from .domain import DomainError, canonical, digest, file_sha256
from .media_tools import MASTER_LABEL, SOUND_RATE, MediaTools
from .route_service import RouteService
from .service import now, require, uid
from .sound_service import STEMS
from .timing_domain import to_samples, to_seconds


LOUDNESS_TARGETS = {-14: "−14 LUFS · web, YouTube", -16: "−16 LUFS · podcast, mobile", -23: "−23 LUFS · EBU R128 truyền hình"}
LOUDNESS_TOLERANCE_LU = 2.0
MAX_TRUE_PEAK_DBTP = -1.0
CHECKS = ("relink", "look", "mix", "watch")
GUIDE = """AI Animation Studio – gói bàn giao cho DaVinci Resolve (bản Free)

1. File > Import > Timeline..., chọn file .fcpxml trong thư mục này. Nếu Resolve không nhận, import file .edl
   (chỉ có hình) rồi kéo các file trong media/audio vào timeline tại 00:00:00:00, mỗi stem một track.
2. Nếu media báo offline: chuột phải clip > Relink Selected Clips..., chọn thư mục media của gói này.
3. Timeline bắt đầu tại 00:00:00:00. Kiểm tra FPS và độ phân giải của timeline khớp với chuẩn series.
4. Áp LUT chung và grain cho mọi shot để thống nhất look; mix âm thanh và đạt loudness mục tiêu.
5. Import phụ đề: File > Import > Subtitle..., chọn file .srt trong thư mục subtitles.
6. Xuất phim, rồi quay lại app (tab Final & xuất phim) để đăng ký file đã xuất và xác nhận C10.

Bản Free không có Super Scale: mọi master trong gói đã ở đúng độ phân giải chuẩn của series.
File timeline chưa được import thử vào Resolve từ môi trường phát triển của app.

----------------------------------------------------------------------------------------------------

AI Animation Studio – hand-off package for DaVinci Resolve (Free edition)

1. File > Import > Timeline..., choose the .fcpxml file in this folder. If Resolve does not accept it, import the
   .edl file (picture only), then drag the files in media/audio onto the timeline at 00:00:00:00, one stem per track.
2. If media shows offline: right-click the clips > Relink Selected Clips..., choose the media folder of this package.
3. The timeline starts at 00:00:00:00. Check that its FPS and resolution match the series standard.
4. Apply the shared LUT and grain to every shot for a consistent look; mix the sound to the target loudness.
5. Import subtitles: File > Import > Subtitle..., choose an .srt file in the subtitles folder.
6. Export the film, then go back to the app (Finals & delivery tab) to register the exported file and confirm C10.

The Free edition has no Super Scale: every master in the package is already at the series' standard resolution.
The timeline file has not been test-imported into Resolve from the app's development environment.
"""


def _place(source: Path, target: Path) -> None:
    """Put a large media file into the package without doubling disk use where the volume allows a hard link."""
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)


class DeliveryService:
    def __init__(self, studio, breakdown, final, sound, *, media=None, opener=None):
        self.studio = studio
        self.db = studio.db
        self.breakdown = breakdown
        self.final = final
        self.sound = sound
        self.media = media or MediaTools(studio)
        self.opener = opener or (lambda path: os.startfile(str(path)))  # noqa: S606 - opens the package folder in Explorer on this PC
        with self.db.transaction() as conn:
            conn.execute("UPDATE episode_delivery_jobs SET state='BLOCKED',error='Backend khởi động lại trong lúc tạo gói; tạo lại.',updated_at=? WHERE state='RUNNING'", (now(),))

    # ---- state -----------------------------------------------------------------------------------------------------

    def _state(self, conn, episode: dict, finals: dict | None = None, sound: dict | None = None) -> dict:
        finals = finals or self.final._state(conn, episode)
        job = conn.execute("SELECT id,state,stage,error,updated_at FROM episode_delivery_jobs WHERE episode_id=? ORDER BY created_at DESC LIMIT 1", (episode["id"],)).fetchone()
        base = {"episode": episode, "ready": False, "blockers": list(finals["blockers"]), "package": None, "export": None, "c10": None, "job": dict(job) if job else None,
                "ffmpeg_configured": self.media.available(), "loudness_targets": [{"lufs": value, "label": label} for value, label in LOUDNESS_TARGETS.items()],
                "master_label": MASTER_LABEL, "summary": {"shots": 0, "approved": 0, "lines": 0, "missing_en": 0}}
        if not finals["ready"]:
            return base
        rough = finals["_rough"]
        sound = sound or self.sound._state(conn, episode, rough)
        plan, root, fps, standards = rough["_plan"], finals["_root"], rough["fps"], finals["standards"]
        blockers = []
        if not (rough["c7"] and rough["c7"]["current"]):
            blockers.append("C7: tập chưa khóa dựng.")
        waiting = [shot["shot_id"] for shot in finals["shots"] if not shot["c8"]["approved"]]
        if waiting:
            blockers.append(f"C8: còn {len(waiting)} shot chưa duyệt final ({', '.join(waiting[:4])}{'…' if len(waiting) > 4 else ''}).")
        build = self.sound.approved_build(sound)
        if not build:
            blockers.append("C9: âm thanh của tập chưa được duyệt.")
        english = plan["language"] == "EN"
        cues = {"vi": [], "en": []}
        for shot in plan["shots"]:
            for line in shot["lines"]:
                start = shot["start_frame"] + line["start_frame"]
                frame = {"line_id": line["line_id"], "start_frame": start, "end_frame": start + line["voice_frames"]}
                cues["vi"].append({**frame, "text": line["original_text"] if english else line["text"]})
                cues["en"].append({**frame, "text": line["text"] if english else line["text_en"]})
        masters = [{"shot_id": shot["shot_id"], "frames": shot["length_frames"], "relative_path": shot["conform"]["relative_path"], "sha256": shot["conform"]["sha256"]}
                   for shot in finals["shots"] if shot["c8"]["approved"]]
        input_hash = digest({"standards": standards, "frames": plan["total_frames"], "masters": [[m["shot_id"], m["sha256"], m["frames"]] for m in masters],
                             "sound": build["_input_hash"] if build else None, "subtitles": {key: [[c["start_frame"], c["end_frame"], c["text"]] for c in value] for key, value in cues.items()}})
        row = conn.execute("SELECT * FROM episode_deliveries WHERE episode_id=? ORDER BY version DESC LIMIT 1", (episode["id"],)).fetchone()
        package = export = c10 = c10_hash = None
        if row:
            inventory = json.loads(row["inventory_json"])
            folder = root / row["relative_dir"]
            package = {"id": row["id"], "version": row["version"], "created_at": row["created_at"], "folder": str(folder), "frames": row["frames"],
                       "seconds": to_seconds(row["frames"], fps), "qc": json.loads(row["qc_json"]), "files": [{"path": item["path"], "bytes": item["bytes"]} for item in inventory],
                       "file_ok": all((folder / item["path"]).is_file() for item in inventory), "current": not blockers and row["input_hash"] == input_hash}
            sent = conn.execute("SELECT * FROM episode_exports WHERE delivery_id=? ORDER BY created_at DESC LIMIT 1", (row["id"],)).fetchone()
            if sent:
                export = {"id": sent["id"], "path": sent["path"], "bytes": sent["bytes"], "sha256": sent["sha256"], "info": json.loads(sent["info_json"]), "created_at": sent["created_at"],
                          "file_ok": Path(sent["path"]).is_file() and Path(sent["path"]).stat().st_size == sent["bytes"]}
                c10_hash = digest({"package": row["input_hash"], "version": row["version"], "export": sent["sha256"]})
        usable = bool(package and package["current"] and package["file_ok"] and package["qc"]["passed"])
        latest = RouteService._checkpoint(conn, episode, "C10")
        if latest:
            c10 = {"sequence": latest["exact_version"], "created_at": latest["created_at"], "current": usable and bool(export) and latest["dependency_hash"] == c10_hash}
        approve = []
        if not usable:
            approve.append("Chưa có gói bàn giao đúng với final và âm thanh hiện tại, qua QC tự động.")
        elif not export:
            approve.append("Chưa đăng ký file phim đã xuất từ DaVinci Resolve.")
        elif not export["file_ok"]:
            approve.append("File phim đã đăng ký không còn ở vị trí cũ hoặc đã đổi; đăng ký lại.")
        warnings = [check["key"] for check in package["qc"]["checks"] if check.get("warning_only") and not check["ok"]] if package else []
        return {**base, "ready": not blockers, "blockers": blockers, "package": package, "export": export, "c10": c10, "c10_hash": c10_hash if usable and export else None,
                "checks": [*CHECKS, *(["qc_warnings"] if warnings else [])], "approve_blockers": approve, "allowed": not approve,
                "standards": standards, "fps": fps, "total_frames": plan["total_frames"], "seconds": to_seconds(plan["total_frames"], fps),
                "summary": {"shots": len(finals["shots"]), "approved": len(masters), "lines": len(cues["vi"]), "missing_en": sum(not cue["text"].strip() for cue in cues["en"])},
                "notes": {"resolve": "App không điều khiển DaVinci Resolve: bạn import timeline, relink media, grade, mix và xuất phim trong Resolve.",
                          "timeline": "File FCPXML và EDL chưa được import thử vào Resolve từ môi trường phát triển; nếu Resolve không nhận FCPXML, dùng EDL (chỉ hình) rồi kéo stem vào tay.",
                          "upscale": "Bản Free không có Super Scale; mọi master trong gói đã ở đúng độ phân giải chuẩn của series."},
                "_input_hash": input_hash, "_masters": masters, "_build": build, "_cues": cues, "_root": root, "_plan": plan}

    @staticmethod
    def _public(state: dict) -> dict:
        return {k: v for k, v in state.items() if not k.startswith("_")}

    def state(self, episode_id: str) -> dict:
        with self.db.connect() as conn:
            return self._public(self._state(conn, self.breakdown._episode(conn, episode_id)))

    # ---- step 13: package ------------------------------------------------------------------------------------------

    def enqueue(self, episode_id: str, loudness_lufs: int) -> dict:
        if loudness_lufs not in LOUDNESS_TARGETS:
            raise DomainError("Chọn mức loudness mục tiêu trong danh sách.")
        self.media.paths()
        with self.db.transaction() as conn:
            episode = self.breakdown._episode(conn, episode_id)
            state = self._state(conn, episode)
            if not state["ready"]:
                raise DomainError("Chưa tạo được gói bàn giao: " + " ".join(state["blockers"]), 409)
            if conn.execute("SELECT 1 FROM episode_delivery_jobs WHERE episode_id=? AND state IN ('QUEUED','RUNNING')", (episode_id,)).fetchone():
                raise DomainError("Đang tạo gói bàn giao cho tập này.", 409)
            request = {"input_hash": state["_input_hash"], "loudness_lufs": loudness_lufs}
            conn.execute("INSERT INTO episode_delivery_jobs(id,project_id,episode_id,operation_id,input_hash,request_json,state,stage,error,created_at,updated_at) VALUES(?,?,?,?,?,?,'QUEUED','PREPARE',NULL,?,?)",
                         (uid(), episode["project_id"], episode_id, digest({**request, "nonce": uid()}), state["_input_hash"], canonical(request), now(), now()))
        return self.state(episode_id)

    def _qc(self, state: dict, preview: Path, stems: dict, target: int, loudness: dict) -> dict:
        fps, total = state["fps"], state["total_frames"]
        samples = to_samples(total, SOUND_RATE, fps)
        info = self.media.probe_media(preview)
        sound = self.media.probe_audio(preview)
        found = self.media.detect(preview)
        video_ms = round(info["frames"] * 1000 * info["fps_den"] / info["fps_num"])
        frame_ms = 1000 * fps["den"] / fps["num"]
        lengths = {role: self.media.stem_info(path)["samples"] for role, path in stems.items()}
        span = lambda item: f"{item['start']:.2f}–{item['end']:.2f} giây" if item["end"] is not None else f"từ {item['start']:.2f} giây tới hết"
        listed = lambda items: ", ".join(span(item) for item in items[:6]) + ("…" if len(items) > 6 else "")
        level, peak = loudness["integrated_lufs"], loudness["true_peak_dbtp"]
        loud_ok = level is not None and abs(level - target) <= LOUDNESS_TOLERANCE_LU and peak is not None and peak <= MAX_TRUE_PEAK_DBTP
        cues, missing = state["summary"]["lines"], state["summary"]["missing_en"]
        checks = [
            {"key": "frames", "label": "Không rơi frame", "ok": info["frames"] == total, "detail": f"{info['frames']}/{total} frame"},
            {"key": "duration", "label": "Đúng thời lượng và FPS", "ok": (info["fps_num"], info["fps_den"]) == (fps["num"], fps["den"]) and abs(video_ms - total * frame_ms) < frame_ms,
             "detail": f"{video_ms / 1000:.3f} giây ở {info['fps_num']}/{info['fps_den']} fps"},
            {"key": "audio_sync", "label": "Audio khớp độ dài hình", "ok": all(value == samples for value in lengths.values()) and abs(sound["duration_ms"] - video_ms) <= frame_ms,
             "detail": f"{len(lengths)} stem × {samples} mẫu ở {SOUND_RATE} Hz; audio preview lệch {abs(sound['duration_ms'] - video_ms)} ms"},
            {"key": "black", "label": "Frame đen", "ok": not found["black"], "warning_only": True, "detail": "không thấy" if not found["black"] else "đoạn đen: " + listed(found["black"])},
            {"key": "freeze", "label": "Đứng hình", "ok": not found["freeze"], "warning_only": True, "detail": "không thấy" if not found["freeze"] else "hình đứng yên: " + listed(found["freeze"])},
            {"key": "loudness", "label": "Loudness của mix tham chiếu", "ok": loud_ok, "warning_only": True,
             "detail": ("mix im lặng" if level is None else f"{level:.1f} LUFS, true peak {peak:.1f} dBTP") + f" · mục tiêu {target} LUFS ±{LOUDNESS_TOLERANCE_LU:g}, true peak ≤ {MAX_TRUE_PEAK_DBTP:g} dBTP"},
            {"key": "subtitles", "label": "Phụ đề", "ok": missing == 0, "warning_only": True, "detail": f"tiếng Việt {cues}/{cues} câu, tiếng Anh {cues - missing}/{cues} câu"},
        ]
        return {"checks": checks, "passed": all(check["ok"] for check in checks if not check.get("warning_only")), "warnings": sum(not check["ok"] for check in checks if check.get("warning_only")),
                "loudness": loudness, "target_lufs": target, "width": info["width"], "height": info["height"]}

    def process_next(self) -> dict | None:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM episode_delivery_jobs WHERE state='QUEUED' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            job = dict(row)
            conn.execute("UPDATE episode_delivery_jobs SET state='RUNNING',updated_at=? WHERE id=?", (now(), job["id"]))
        request = json.loads(job["request_json"])
        work = self.db.data_root / "delivery-work" / job["operation_id"]
        folder = None

        def stage(name: str) -> None:
            with self.db.transaction() as conn:
                conn.execute("UPDATE episode_delivery_jobs SET stage=?,updated_at=? WHERE id=?", (name, now(), job["id"]))
        try:
            with self.db.connect() as conn:
                episode = self.breakdown._episode(conn, job["episode_id"])
                state = self._state(conn, episode)
                if not state["ready"] or state["_input_hash"] != job["input_hash"]:
                    raise DomainError("Final, âm thanh hoặc khóa dựng đã đổi sau khi xếp việc tạo gói; tạo lại.", 409)
                version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM episode_deliveries WHERE episode_id=?", (job["episode_id"],)).fetchone()[0]
            root, code, fps, standards = state["_root"], episode["code"], state["fps"], state["standards"]
            relative_dir = f"delivery/{code}/v{version}"
            folder = root / relative_dir
            if folder.exists():
                raise DomainError("Đã có thư mục gói cùng phiên bản trong dự án; kiểm tra thư mục delivery.", 409)
            inventory, shots = [], []
            for master in state["_masters"]:
                source = root / master["relative_path"]
                if not source.is_file() or file_sha256(source) != master["sha256"]:
                    raise DomainError(f"{master['shot_id']}: master thiếu hoặc đã bị đổi sau khi duyệt C8.", 409)
                target = folder / "media" / "video" / f"{master['shot_id']}.mov"
                _place(source, target)
                shots.append({"shot_id": master["shot_id"], "path": target, "frames": master["frames"]})
                inventory.append({"path": f"media/video/{target.name}", "bytes": target.stat().st_size, "sha256": master["sha256"]})
            build = state["_build"]
            stems, mix = {}, root / build["_stems"]["MIX"]["relative_path"]
            for role, _ in STEMS:
                item = build["_stems"][role]
                source = root / item["relative_path"]
                if not source.is_file() or file_sha256(source) != item["sha256"]:
                    raise DomainError(f"Stem {role} thiếu hoặc đã bị đổi sau khi duyệt C9.", 409)
                stems[role] = folder / "media" / "audio" / source.name
                _place(source, stems[role])
                inventory.append({"path": f"media/audio/{source.name}", "bytes": item["bytes"], "sha256": item["sha256"]})
            written = {f"{code}.fcpxml": fcpxml(code, fps, standards["width"], standards["height"], shots, [{"role": role, "path": path} for role, path in stems.items()]),
                       f"{code}.edl": edl(code, fps, shots), f"subtitles/{code}.vi.srt": srt(state["_cues"]["vi"], fps), f"subtitles/{code}.en.srt": srt(state["_cues"]["en"], fps),
                       "HUONG-DAN-RESOLVE.txt": GUIDE}
            for name, text in written.items():
                (folder / name).parent.mkdir(parents=True, exist_ok=True)
                (folder / name).write_text(text, encoding="utf-8-sig" if name.endswith(".txt") else "utf-8", newline="\n")
            stage("PREVIEW")
            preview = folder / f"{code}_preview.mp4"
            self.media.export_preview([shot["path"] for shot in shots], mix, preview, work)
            stage("QC")
            qc = self._qc(state, preview, stems, request["loudness_lufs"], self.media.loudness(mix))
            (folder / "qc.json").write_text(json.dumps(qc, ensure_ascii=False, indent=2), encoding="utf-8")
            for name in (*written, preview.name, "qc.json"):
                inventory.append({"path": name, "bytes": (folder / name).stat().st_size, "sha256": file_sha256(folder / name)})
            manifest = {"schema_version": "v45.delivery.1", "episode": code, "version": version, "input_hash": job["input_hash"], "fps": fps, "standards": standards,
                        "total_frames": state["total_frames"], "timeline_start": "00:00:00:00", "master_codec": MASTER_LABEL, "files": inventory,
                        "resolve_import_verified": False}
            (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            with self.db.transaction() as conn:
                conn.execute("INSERT INTO episode_deliveries(id,project_id,episode_id,version,input_hash,relative_dir,inventory_json,qc_json,frames,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                             (uid(), job["project_id"], job["episode_id"], version, job["input_hash"], relative_dir, canonical(inventory), canonical(qc), state["total_frames"], now()))
                conn.execute("UPDATE episode_delivery_jobs SET state='COMPLETED',error=NULL,updated_at=? WHERE id=?", (now(), job["id"]))
                self.studio.audit(conn, job["project_id"], "delivery.built", job["episode_id"], {"version": version, "qc_passed": qc["passed"], "warnings": qc["warnings"]})
        except Exception as exc:
            if folder:
                shutil.rmtree(folder, ignore_errors=True)
            with self.db.transaction() as conn:
                conn.execute("UPDATE episode_delivery_jobs SET state='BLOCKED',error=?,updated_at=? WHERE id=?",
                             (str(exc)[:500] if isinstance(exc, DomainError) else f"{type(exc).__name__}: không tạo được gói bàn giao.", now(), job["id"]))
        finally:
            shutil.rmtree(work, ignore_errors=True)
        with self.db.connect() as conn:
            return dict(conn.execute("SELECT id,state,stage,error,updated_at FROM episode_delivery_jobs WHERE id=?", (job["id"],)).fetchone())

    def _package(self, conn, episode_id: str) -> tuple[dict, dict]:
        episode = self.breakdown._episode(conn, episode_id)
        state = self._state(conn, episode)
        if not state["package"]:
            raise DomainError("Tập này chưa có gói bàn giao.", 409)
        return episode, state

    def preview_file(self, delivery_id: str) -> Path:
        with self.db.connect() as conn:
            row = require(conn.execute("SELECT d.*,e.code FROM episode_deliveries d JOIN episodes e ON e.id=d.episode_id WHERE d.id=?", (delivery_id,)).fetchone(), "delivery")
        path = Path(self.studio.project(row["project_id"])["root"]).resolve() / row["relative_dir"] / f"{row['code']}_preview.mp4"
        if not path.is_file():
            raise DomainError("MP4 preview thiếu trong thư mục gói; tạo lại gói.", 409)
        return path

    def open_folder(self, episode_id: str) -> dict:
        with self.db.connect() as conn:
            _, state = self._package(conn, episode_id)
        folder = Path(state["package"]["folder"])
        if not folder.is_dir():
            raise DomainError("Thư mục gói không còn trong dự án; tạo lại gói.", 409)
        try:
            self.opener(folder)
        except OSError as exc:
            raise DomainError("Không mở được thư mục gói bằng Explorer; mở tay theo đường dẫn hiển thị.", 409) from exc
        return {"opened": str(folder)}

    # ---- C10 -------------------------------------------------------------------------------------------------------

    def register_export(self, episode_id: str, source_path: str) -> dict:
        """The film exported from Resolve. It is measured and hashed where it is; nothing is copied or changed."""
        source = Path(source_path)
        if not source.is_file() or source.suffix.lower() not in (".mp4", ".mov"):
            raise DomainError("Chọn file phim MP4 hoặc MOV đã xuất từ DaVinci Resolve.")
        with self.db.connect() as conn:
            episode, state = self._package(conn, episode_id)
        package = state["package"]
        if not (package["current"] and package["file_ok"] and package["qc"]["passed"]):
            raise DomainError("Gói bàn giao đã cũ hoặc chưa qua QC tự động; tạo lại gói trước khi đăng ký phim đã xuất.", 409)
        info = self.media.probe_media(source)
        standards, fps = state["standards"], state["fps"]
        notes = []
        if info["frames"] != package["frames"]:
            notes.append(f"Phim có {info['frames']} frame, timeline của gói có {package['frames']} frame (bình thường nếu bạn thêm tiêu đề hoặc credit).")
        if (info["width"], info["height"]) != (standards["width"], standards["height"]):
            notes.append(f"Phim {info['width']}×{info['height']}, chuẩn series {standards['width']}×{standards['height']}.")
        if (info["fps_num"] * fps["den"]) != (fps["num"] * info["fps_den"]):
            notes.append(f"Phim {info['fps_num']}/{info['fps_den']} fps, chuẩn series {fps['num']}/{fps['den']} fps.")
        if not info["audio_streams"]:
            notes.append("Phim không có luồng âm thanh.")
        info["notes"] = notes
        sha256, size = file_sha256(source), source.stat().st_size  # A film can be several GB; hash it before taking the write lock.
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO episode_exports(id,project_id,episode_id,delivery_id,path,sha256,bytes,info_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                         (uid(), episode["project_id"], episode_id, package["id"], str(source.resolve()), sha256, size, canonical(info), now()))
            self.studio.audit(conn, episode["project_id"], "delivery.export_registered", episode_id, {"package_version": package["version"]})
        return self.state(episode_id)

    def approve_c10(self, episode_id: str, expected_hash: str, checks: dict) -> dict:
        with self.db.connect() as conn:
            registered = self._package(conn, episode_id)[1]["export"]
        if registered and registered["file_ok"] and file_sha256(Path(registered["path"])) != registered["sha256"]:
            raise DomainError("File phim đã đổi sau khi đăng ký; đăng ký lại.", 409)
        with self.db.transaction() as conn:
            episode, state = self._package(conn, episode_id)
            wanted = state["checks"]
            if not isinstance(checks, dict) or set(checks) != set(wanted) or any(checks[key] is not True for key in wanted):
                raise DomainError("Xác nhận đủ các mục đã làm trong DaVinci Resolve: import và relink, LUT và grain, mix và loudness, xem lại bản xuất"
                                  + (", đã xem cảnh báo QC." if "qc_warnings" in wanted else "."))
            if state["approve_blockers"]:
                raise DomainError("Chưa xác nhận được C10: " + " ".join(state["approve_blockers"]), 409)
            if state["c10_hash"] != expected_hash:
                raise DomainError("Gói hoặc file phim đã đổi sau khi bạn xem; tải lại.", 409)
            if state["c10"] and state["c10"]["current"]:
                raise DomainError("Tập này đã được xác nhận xuất phim.", 409)
            package, export = state["package"], state["export"]
            if export["sha256"] != registered["sha256"]:
                raise DomainError("File phim đã đăng ký vừa đổi; tải lại.", 409)
            workflow.record_checkpoint(self.studio, conn, episode["project_id"], "C10", episode_id, (state["c10"]["sequence"] if state["c10"] else 0) + 1, expected_hash,
                                       {"package_version": package["version"], "qc_warnings": package["qc"]["warnings"], "export_sha256": export["sha256"], "export_bytes": export["bytes"],
                                        "export_frames": export["info"]["frames"], "export_notes": export["info"]["notes"], "checks": wanted,
                                        "resolve": "OPERATOR_CONFIRMED", "resolve_import_verified_by_app": False})
        return self.state(episode_id)
