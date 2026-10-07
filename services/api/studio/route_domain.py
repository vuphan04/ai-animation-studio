"""Pure rules for workflow V4.5 step 05/C3: suggest UE / AI / Hybrid for a shot and estimate what it costs.

A suggestion is advice. Only the operator's locked decision counts, and cost figures are estimates from the
price table the operator saved, never provider billing.
"""

from __future__ import annotations

import math

from .domain import DomainError
from .series_domain import VIDEO_MODELS


ROUTES = ("UE", "AI", "HYBRID")
CURRENCIES = ("USD", "CNY")
MODEL_LABELS = {"SEEDANCE_2_5": "Seedance 2.5", "MINIMAX_H3": "MiniMax H3"}
# Clip limits written on the V4.5 diagram. A shorter AI shot is generated at the minimum, then trimmed.
MODEL_LIMITS = {"SEEDANCE_2_5": {"min_seconds": 4, "max_seconds": None},
                "MINIMAX_H3": {"min_seconds": 5, "max_seconds": 15}}
WIDE = ("EXTREME_WIDE", "WIDE")
CLOSE = ("CLOSE_UP", "EXTREME_CLOSE_UP")
PRICE_FIELDS = ("draft_micro_per_second", "final_micro_per_second", "input_video_micro_per_second")
EMPTY_PRICES = {"video": {model: {"currency": "USD", **{field: 0 for field in PRICE_FIELDS}} for model in VIDEO_MODELS},
                "voice": {"currency": "USD", "micro_per_1k_chars": 0}}
# Offered in the form until the operator saves a table. MiniMax H3 figures are the 03/10/2026 notes of the
# diagram (768P draft, regenerate to 2K plus the source-video fee); the diagram gives no Seedance price.
SUGGESTED_PRICES = {"video": {"SEEDANCE_2_5": {"currency": "USD", "draft_micro_per_second": 0, "final_micro_per_second": 0,
                                               "input_video_micro_per_second": 0},
                              "MINIMAX_H3": {"currency": "USD", "draft_micro_per_second": 80_000, "final_micro_per_second": 100_000,
                                             "input_video_micro_per_second": 50_000}},
                    "voice": {"currency": "USD", "micro_per_1k_chars": 0}}


def clean_prices(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"video", "voice"} or not isinstance(value["video"], dict) or set(value["video"]) != set(VIDEO_MODELS):
        raise DomainError("Bảng giá cần đủ các model video và giá thoại.")
    video = {}
    for model in VIDEO_MODELS:
        item = value["video"][model]
        if not isinstance(item, dict) or item.get("currency") not in CURRENCIES or any(type(item.get(f)) is not int or not 0 <= item[f] <= 10**9 for f in PRICE_FIELDS):
            raise DomainError(f"Giá của {MODEL_LABELS[model]} phải là số không âm theo USD hoặc CNY.")
        video[model] = {"currency": item["currency"], **{field: item[field] for field in PRICE_FIELDS}}
    voice = value["voice"]
    if not isinstance(voice, dict) or voice.get("currency") not in CURRENCIES or type(voice.get("micro_per_1k_chars")) is not int or not 0 <= voice["micro_per_1k_chars"] <= 10**9:
        raise DomainError("Giá thoại mỗi 1.000 ký tự phải là số không âm theo USD hoặc CNY.")
    return {"video": video, "voice": {"currency": voice["currency"], "micro_per_1k_chars": voice["micro_per_1k_chars"]}}


def suggest(facts: dict, labels: dict) -> dict:
    """The diagram's hard rules, in order: what forces UE, then what makes AI the better offer."""
    missing = ", ".join(labels.get(key, key) for key in facts["missing_assets"])
    forced = []
    if facts["main_dialogue"] and facts["face_visible"] and facts["shot_size"] in CLOSE:
        forced.append("Thoại quay cận mặt nhân vật chính → UE.")
    if facts["content_flags"]:
        forced.append("Bạo lực, máu me → UE (model AI dễ từ chối tạo).")
    if forced:
        warnings = [f"Quy tắc yêu cầu UE nhưng còn thiếu asset: {missing}. Bổ sung asset hoặc tự chọn AI."] if missing else []
        return {"route": "UE", "reasons": forced, "warnings": warnings}
    reasons = []
    if missing:
        reasons.append(f"Thiếu tài nguyên UE ({missing}) → đề xuất AI.")
    if facts["shot_size"] in WIDE:
        reasons.append("Shot rộng → đề xuất AI.")
    if not facts["face_visible"]:
        reasons.append("Không lộ mặt → đề xuất AI.")
    if reasons:
        return {"route": "AI", "reasons": reasons, "warnings": []}
    if facts["missing_vfx"]:
        return {"route": "HYBRID", "warnings": [],
                "reasons": [f"Đủ nhân vật và bối cảnh trong UE nhưng thư viện chưa có VFX ({', '.join(facts['missing_vfx'])}) → Hybrid: UE làm nguồn, AI thêm hiệu ứng."]}
    return {"route": "UE", "reasons": ["Đủ tài nguyên UE và shot lộ mặt ở cỡ cảnh gần → UE."], "warnings": []}


def review(facts: dict, route: str, model: str | None, prices: dict, labels: dict) -> dict:
    """Cost, blockers and notes of one shot under a route. A shot with issues cannot be locked."""
    voice = prices["voice"]
    estimate = {"generate_seconds": None, "padded_seconds": 0, "draft_micro": 0, "final_micro": 0, "input_micro": 0,
                "video_micro": 0, "video_currency": None,
                "voice_micro": (facts["dialogue_chars"] * voice["micro_per_1k_chars"] + 999) // 1000, "voice_currency": voice["currency"]}
    issues, warnings, notes = [], [], []
    if route in ("UE", "HYBRID") and facts["missing_assets"]:
        issues.append("Thiếu asset UE: " + ", ".join(labels.get(key, key) for key in facts["missing_assets"])
                      + ". Bổ sung ở Thư viện series rồi duyệt lại C2, hoặc chọn AI.")
    kimodo = facts["missing_motion"] if route in ("UE", "HYBRID") else []
    over_max = False
    if kimodo:
        notes.append("Thiếu motion phù hợp (" + ", ".join(kimodo) + "): đánh dấu tạo bằng Kimodo ở bước 08.")
    if route != "UE":
        if model not in VIDEO_MODELS:
            issues.append("Chọn model video cho shot AI/Hybrid.")
        else:
            label, limits, price = MODEL_LABELS[model], MODEL_LIMITS[model], prices["video"][model]
            needed = math.ceil(facts["duration_seconds"])
            seconds = max(limits["min_seconds"], needed)
            over_max = bool(limits["max_seconds"]) and needed > limits["max_seconds"]
            if over_max:
                issues.append(f"Shot dài {facts['duration_seconds']:g} giây, vượt clip tối đa {limits['max_seconds']} giây của {label}; tách shot ở C1, đổi model hoặc chọn UE.")
            if not price["draft_micro_per_second"] or not price["final_micro_per_second"]:
                issues.append(f"Chưa có giá draft và final của {label} trong bảng giá.")
            if route == "HYBRID" and not price["input_video_micro_per_second"]:
                issues.append(f"Chưa có giá video đầu vào của {label}; Hybrid cần giá này để tính chi phí.")
            if seconds > needed or facts["duration_seconds"] < limits["min_seconds"]:
                notes.append(f"Ngắn hơn mức tối thiểu {limits['min_seconds']} giây của {label}: tạo {seconds} giây rồi cắt, tính phí theo thời lượng tạo.")
            # The control video is an input of both the draft and the final request.
            input_micro = 2 * seconds * price["input_video_micro_per_second"] if route == "HYBRID" else 0
            draft, final = seconds * price["draft_micro_per_second"], seconds * price["final_micro_per_second"]
            estimate.update(generate_seconds=seconds, padded_seconds=round(seconds - facts["duration_seconds"], 2),
                            draft_micro=draft, final_micro=final, input_micro=input_micro,
                            video_micro=draft + final + input_micro, video_currency=price["currency"])
        if facts["content_flags"]:
            warnings.append("Có bạo lực hoặc máu me: model AI dễ từ chối tạo; quy tắc đề xuất UE.")
        if route == "AI" and facts["main_dialogue"] and facts["face_visible"] and facts["shot_size"] in CLOSE:
            warnings.append("Thoại quay cận mặt nhân vật chính: quy tắc đề xuất UE để kiểm soát khẩu hình.")
    return {"estimate": estimate, "issues": issues, "warnings": warnings, "notes": notes,
            "needs_kimodo": bool(kimodo), "kimodo_tags": list(kimodo), "over_max": over_max}
