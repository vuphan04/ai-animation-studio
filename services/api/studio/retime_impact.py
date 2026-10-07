"""Workflow V4.5: changing dialogue or timing after shots were built.

Approving a new C4 sends every shot whose timing changed back to the step that has to be redone; shots
that did not change keep their builds, drafts and finals. This module lists that work and what regenerating
costs, so the operator approves the update knowingly. What the operator fixed by hand stays as it is:
routes locked at C3, animation chosen or approved at step 08, prompts and references of AI shots.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import budget
from .domain import digest
from .route_domain import MODEL_LABELS, review
from .timing_domain import to_seconds


KEPT = "Giữ nguyên: phương thức đã khóa ở C3, animation bạn đã chọn hoặc duyệt, prompt và ảnh tham chiếu của shot AI."


def changes(old: dict, new: dict) -> tuple[dict, list[dict]]:
    """Per shot ID, what differs between two timings; and the shots that are gone."""
    before = {shot["shot_id"]: shot for shot in old["shots"]}
    spoken = lambda shot: [(line["line_id"], line["sha256"], line["pause_before_frames"], line["voice_frames"]) for line in shot["lines"]]
    changed = {}
    for shot in new["shots"]:
        prior = before.pop(shot["shot_id"], None)
        found = []
        if prior is None:
            found.append(("NEW", "shot mới trong timing"))
        elif old["fps"] != new["fps"]:
            found.append(("LENGTH", "chuẩn FPS của series đổi"))
        else:
            if prior["length_frames"] != shot["length_frames"]:
                found.append(("LENGTH", f"độ dài {prior['length_frames']} → {shot['length_frames']} frame"))
            if spoken(prior) != spoken(shot):
                found.append(("DIALOGUE", "thoại hoặc khoảng nghỉ đổi"))
            if prior["cues"] != shot["cues"]:
                found.append(("CUES", "mốc hành động đổi"))
        if found:
            changed[shot["shot_id"]] = found
    return changed, [{"shot_id": shot_id, "scene_key": shot["scene_key"]} for shot_id, shot in sorted(before.items())]


def impact(conn, episode: dict, plan: dict, old: dict, new: dict) -> dict:
    changed, removed = changes(old, new)
    eid, fps = episode["id"], new["fps"]
    decided = {shot["shot_id"]: shot for scene in plan["scenes"] for shot in scene["shots"]}
    timed = {shot["shot_id"]: shot for shot in new["shots"]}
    built = {row["shot_id"]: row for row in conn.execute("SELECT * FROM ue_shot_states WHERE episode_id=?", (eid,))}
    baked = {row[0] for row in conn.execute("SELECT DISTINCT shot_id FROM ue_cache_states WHERE episode_id=? AND status='BAKED'", (eid,))}
    proxies = {row[0] for row in conn.execute("SELECT DISTINCT shot_id FROM ue_proxies WHERE episode_id=?", (eid,))}
    drafts = {row["shot_id"]: row for row in conn.execute(
        "SELECT t.* FROM ai_shot_inputs i JOIN ai_video_takes t ON t.id=i.c6_take_id WHERE i.episode_id=?", (eid,))}
    finals = {row[0] for row in conn.execute("SELECT DISTINCT shot_id FROM final_takes WHERE episode_id=?", (eid,))}
    locked = {row[0] for row in conn.execute("SELECT scene_key FROM scene_picture_locks WHERE episode_id=?", (eid,))}
    foley = {row[0] for row in conn.execute("SELECT DISTINCT shot_id FROM episode_sound_clips WHERE episode_id=? AND kind='FOLEY' AND enabled=1", (eid,))}
    current_time = datetime.now(timezone.utc)
    shots, sums = [], {}

    def paid(work: list, text: str, micro: int, currency: str) -> None:
        work.append({"text": text, "micro": micro, "currency": currency})
        sums[currency] = sums.get(currency, 0) + micro

    for shot_id, found in changed.items():
        route = decided.get(shot_id)
        if not route:
            continue
        kinds = {kind for kind, _ in found}
        resized = bool(kinds & {"LENGTH", "NEW"})
        work = []
        if route["route"] in ("UE", "HYBRID"):
            row = built.get(shot_id)
            if row and (row["assembled_hash"] or row["animated_hash"]):
                work.append({"text": "Cập nhật sequence trong Unreal: độ dài, audio thoại, animation/facial và camera của shot."})
            if row and row["c5_hash"]:
                work.append({"text": "Duyệt lại chuyển động của shot (C5)."})
            if shot_id in baked:
                work.append({"text": "Bake/cache lại tóc, vải, VFX đã khai báo."})
            if shot_id in proxies:
                work.append({"text": "Render lại proxy" + (" và chốt lại C6." if route["route"] == "UE" else "; proxy mới thành frame đầu của draft.")})
            if route["route"] == "UE" and shot_id in finals:
                work.append({"text": "Render lại final UE (không có phí nhà cung cấp)."})
        if route["route"] in ("AI", "HYBRID"):
            draft = drafts.get(shot_id)
            seconds = to_seconds(timed[shot_id]["length_frames"], fps)
            estimate = review(dict(route["facts"], duration_seconds=seconds), route["route"], route["video_model"], plan["prices"], plan["labels"])["estimate"]
            price = plan["prices"]["video"].get(route["video_model"]) if route["video_model"] else None
            label = MODEL_LABELS.get(route["video_model"], "model video")
            if draft and price and estimate["generate_seconds"]:
                expired = bool(draft["expires_at"]) and datetime.fromisoformat(draft["expires_at"]) <= current_time
                short = draft["requested_seconds"] < seconds
                refinal = shot_id in finals and resized
                new_draft = True
                if route["route"] == "HYBRID" and (built.get(shot_id) or shot_id in proxies):
                    paid(work, f"Nguồn UE của shot đổi: tạo draft {label} mới từ nguồn mới.", estimate["draft_micro"], price["currency"])
                elif short:
                    paid(work, f"Draft dài {draft['requested_seconds']} giây, shot cần {seconds:g} giây: tạo draft {label} mới cho phần thiếu.", estimate["draft_micro"], price["currency"])
                elif refinal and expired:
                    paid(work, f"Draft {label} đã quá hạn 7 ngày để tạo final: tạo draft mới.", estimate["draft_micro"], price["currency"])
                else:
                    new_draft = False
                    if resized:
                        work.append({"text": "Draft đủ dài: app cắt lại theo độ dài mới, không tốn phí."})
                if shot_id in finals and (new_draft or refinal):
                    generated = estimate["generate_seconds"] if new_draft else draft["requested_seconds"]
                    paid(work, f"Tạo lại final {label}.", generated * price["final_micro_per_second"], price["currency"])
        if shot_id in foley and resized:
            work.append({"text": "Foley của shot: tạo lại hoặc xác nhận còn khớp hình."})
        shots.append({"shot_id": shot_id, "scene_key": timed[shot_id]["scene_key"], "route": route["route"], "changes": [text for _, text in found], "work": work})
    touched = sorted({shot["scene_key"] for shot in shots} | {shot["scene_key"] for shot in removed})
    scenes = [f"{scene}: mở khóa dựng; ghép lại rough cut rồi khóa lại." for scene in touched if scene in locked]
    episode_work = []
    if scenes and conn.execute("SELECT 1 FROM workflow_checkpoints WHERE project_id=? AND checkpoint='C7' AND scope_id=? LIMIT 1", (episode["project_id"], eid)).fetchone():
        episode_work.append("Khóa dựng cả tập (C7) phải duyệt lại sau khi các scene trên khóa lại.")
    if shots or removed:
        if conn.execute("SELECT 1 FROM episode_sound_builds WHERE episode_id=? LIMIT 1", (eid,)).fetchone():
            episode_work.append("Cập nhật âm thanh: ghép lại stem theo timing mới và duyệt lại C9.")
        if conn.execute("SELECT 1 FROM episode_deliveries WHERE episode_id=? LIMIT 1", (eid,)).fetchone():
            episode_work.append("Tạo lại gói timeline, phụ đề và QC (bước 13).")
    totals = [{"currency": currency, "micro": amount} for currency, amount in sorted(sums.items())]
    content = {"shots": shots, "removed": [shot["shot_id"] for shot in removed], "scenes": scenes, "episode": episode_work, "totals": totals}
    return {**content, "hash": digest(content), "kept": KEPT,
            "requires_ack": bool(scenes or episode_work or any(shot["work"] for shot in shots)),
            "budget": [{"currency": t["currency"], **budget.check(conn, episode["project_id"], t["currency"], t["micro"])} for t in totals]}
