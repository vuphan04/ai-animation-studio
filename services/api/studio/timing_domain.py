"""Pure timing rules for workflow V4.5 checkpoint C4: shot lengths, pauses and action cues in whole frames.

A line lasts ceil(samples × fps / sample_rate) frames. Audio is never cut or stretched to fit a shot; the
shot is made long enough instead.
"""

from __future__ import annotations

from .domain import DomainError


SCHEMA = "timing.1"
LEAD_SECONDS, GAP_SECONDS, TAIL_SECONDS = 0.25, 0.35, 0.5
MAX_SHOT_SECONDS = 120
MAX_CUES = 20


def duration_frames(sample_count: int, sample_rate: int, fps: dict) -> int:
    """Whole frames a take occupies, rounded up so no audio falls outside its line."""
    if min(sample_count, sample_rate, fps["num"], fps["den"]) <= 0:
        raise DomainError("Thông số WAV/FPS không hợp lệ.")
    numerator = sample_count * fps["num"]
    denominator = sample_rate * fps["den"]
    return (numerator + denominator - 1) // denominator


def to_frames(seconds: float, fps: dict) -> int:
    return max(1, round(seconds * fps["num"] / fps["den"]))


def to_seconds(frames: int, fps: dict) -> float:
    return round(frames * fps["den"] / fps["num"], 3)


def to_samples(frames: int, sample_rate: int, fps: dict) -> int:
    return (frames * sample_rate * fps["den"] + fps["num"] // 2) // fps["num"]


def required_frames(shot: dict) -> int:
    return sum(line["pause_before_frames"] + line["voice_frames"] for line in shot["lines"])


def propose(content: dict, episode_code: str, selected: dict, fps: dict, prior: dict | None, breakdown_hash: str) -> dict:
    """Lay every shot out from the takes in use. Pauses, cues and lengths the operator already set are kept
    wherever they still fit, so running this again on an up-to-date timing changes nothing."""
    old = {shot["shot_id"]: shot for shot in prior["shots"]} if prior and prior["fps"] == fps else {}
    shots = []
    for scene in content["scenes"]:
        for shot in scene["shots"]:
            shot_id = f"{episode_code}_{scene['scene_key']}_{shot['shot_key']}"
            before = old.get(shot_id)
            pauses = {line["line_id"]: line["pause_before_frames"] for line in before["lines"]} if before else {}
            lines = []
            for position, line in enumerate(shot["dialogue"]):
                line_id = f"{episode_code}_{scene['scene_key']}_{line['line_key']}"
                take = selected[line_id]
                lines.append({"line_id": line_id, "take_id": take["id"], "sha256": take["sha256"], "sample_rate": take["sample_rate"],
                              "sample_count": take["sample_count"], "voice_frames": duration_frames(take["sample_count"], take["sample_rate"], fps),
                              "pause_before_frames": pauses.get(line_id, to_frames(LEAD_SECONDS if position == 0 else GAP_SECONDS, fps))})
            needed = sum(line["pause_before_frames"] + line["voice_frames"] for line in lines)
            if before and before["length_frames"] >= max(needed, 1):
                length = before["length_frames"]
            else:
                length = max(to_frames(shot["duration_seconds"], fps), needed + (to_frames(TAIL_SECONDS, fps) if lines else 0))
            shots.append({"shot_id": shot_id, "scene_key": scene["scene_key"], "length_frames": length, "lines": lines,
                          "cues": [cue for cue in before["cues"] if cue["frame"] < length] if before else []})
    return {"schema_version": SCHEMA, "fps": fps, "breakdown_hash": breakdown_hash, "shots": shots}


def edit_shot(shot: dict, length_frames: object, pauses: object, cues: object, fps: dict) -> dict:
    limit = to_frames(MAX_SHOT_SECONDS, fps)
    if type(length_frames) is not int or not isinstance(pauses, dict) or not isinstance(cues, list):
        raise DomainError("Độ dài shot, khoảng nghỉ và mốc hành động không đúng dạng.")
    if set(pauses) != {line["line_id"] for line in shot["lines"]}:
        raise DomainError("Khoảng nghỉ phải khai cho đúng các câu thoại của shot.", 409)
    lines = []
    for line in shot["lines"]:
        pause = pauses[line["line_id"]]
        if type(pause) is not int or not 0 <= pause <= limit:
            raise DomainError(f"Khoảng nghỉ trước mỗi câu phải từ 0 đến {limit} frame.")
        lines.append(dict(line, pause_before_frames=pause))
    needed = sum(line["pause_before_frames"] + line["voice_frames"] for line in lines)
    if length_frames < max(needed, 1):
        raise DomainError(f"Thoại và khoảng nghỉ cần {needed} frame, vượt độ dài shot {max(needed, 1) - length_frames} frame. Tăng độ dài shot hoặc giảm khoảng nghỉ; app không cắt thoại.", 409)
    if length_frames > limit:
        raise DomainError(f"Độ dài shot tối đa {limit} frame ({MAX_SHOT_SECONDS} giây).")
    if len(cues) > MAX_CUES:
        raise DomainError(f"Tối đa {MAX_CUES} mốc hành động cho mỗi shot.")
    clean = []
    for cue in cues:
        if not isinstance(cue, dict) or set(cue) != {"label", "frame"} or not isinstance(cue["label"], str) or not 1 <= len(cue["label"].strip()) <= 80 \
                or type(cue["frame"]) is not int or not 0 <= cue["frame"] < length_frames:
            raise DomainError("Mỗi mốc hành động cần mô tả (tối đa 80 ký tự) và frame nằm trong shot.")
        clean.append({"label": cue["label"].strip(), "frame": cue["frame"]})
    return dict(shot, length_frames=length_frames, lines=lines, cues=sorted(clean, key=lambda cue: (cue["frame"], cue["label"])))
