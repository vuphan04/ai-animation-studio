"""Operator configuration of the AI video models used by workflow V4.5 (Seedance 2.5, MiniMax H3).

Saved means the platform, model ID and key are present. It does not mean a request has ever succeeded;
`verified_at` is set only after this app has received a draft from that model.
"""

from __future__ import annotations

import re

from .domain import DomainError
from .secrets import delete_provider_key, get_provider_key, prompt_provider_key
from .service import now


MODELS = {
    "SEEDANCE_2_5": {"label": "Seedance 2.5", "platforms": {
        "BYTEPLUS": {"label": "BytePlus ModelArk", "base_url": "https://ark.ap-southeast.bytepluses.com", "secret": "SEEDANCE_BYTEPLUS"},
        "VOLCENGINE": {"label": "Volcengine Ark", "base_url": "https://ark.cn-beijing.volces.com", "secret": "SEEDANCE_ARK"}}},
    "MINIMAX_H3": {"label": "MiniMax H3", "platforms": {
        "GLOBAL": {"label": "MiniMax quốc tế", "base_url": "https://api.minimax.io", "secret": "MINIMAX"},
        "CHINA": {"label": "MiniMax Trung Quốc", "base_url": "https://api.minimaxi.com", "secret": "MINIMAX"}}},
}
MODEL_ID = re.compile(r"[a-zA-Z0-9_.:-]{1,128}")


class VideoModelSettings:
    def __init__(self, studio, key_reader=None):
        self.studio = studio
        self.db = studio.db
        self.key_reader = key_reader or get_provider_key

    def _has_key(self, secret: str) -> bool:
        try:
            return bool(self.key_reader(secret))
        except Exception:
            return False

    def one(self, model: str) -> dict:
        if model not in MODELS:
            raise DomainError("Model video không hợp lệ.")
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM video_model_settings WHERE model=?", (model,)).fetchone()
        platform = row["platform"] if row else "UNCONFIGURED"
        target = MODELS[model]["platforms"].get(platform)
        key = bool(target) and self._has_key(target["secret"])
        return {"model": model, "label": MODELS[model]["label"], "platform": platform, "model_id": row["model_id"] if row else "",
                "revision": row["revision"] if row else 0, "key_configured": key, "configured": bool(target and key and row["model_id"]),
                "verified_at": row["verified_at"] if row else None, "base_url": target["base_url"] if target else None,
                "secret": target["secret"] if target else None,
                "platforms": [{"id": key_, "label": value["label"]} for key_, value in MODELS[model]["platforms"].items()]}

    def get(self) -> dict:
        return {"models": [{k: v for k, v in self.one(model).items() if k not in ("secret", "base_url")} for model in MODELS],
                "note": "Đã lưu chỉ xác nhận có nền tảng, model ID và khóa. Một model chỉ được ghi là đã nhận draft sau khi app tải về được video từ model đó."}

    def save(self, model: str, expected_revision: int, platform: str, model_id: str) -> dict:
        if model not in MODELS or platform not in ("UNCONFIGURED", *MODELS[model]["platforms"]) or not isinstance(model_id, str) \
                or (platform != "UNCONFIGURED" and not MODEL_ID.fullmatch(model_id)):
            raise DomainError("Chọn nền tảng và nhập model ID (hoặc endpoint ID) đúng như trong tài khoản nhà cung cấp; không nhập URL hay khóa.")
        with self.db.transaction() as conn:
            row = conn.execute("SELECT revision,platform,model_id FROM video_model_settings WHERE model=?", (model,)).fetchone()
            if (row["revision"] if row else 0) != expected_revision:
                raise DomainError("Cấu hình model video đã đổi ở phiên khác; tải lại trước khi lưu.", 409)
            changed = not row or (row["platform"], row["model_id"]) != (platform, model_id)
            conn.execute("INSERT INTO video_model_settings(model,platform,model_id,revision,verified_at,updated_at) VALUES(?,?,?,?,NULL,?) "
                         "ON CONFLICT(model) DO UPDATE SET platform=excluded.platform,model_id=excluded.model_id,revision=excluded.revision,"
                         "verified_at=CASE WHEN ? THEN NULL ELSE verified_at END,updated_at=excluded.updated_at",
                         (model, platform, model_id if platform != "UNCONFIGURED" else "", expected_revision + 1, now(), int(changed)))
            self.studio.audit(conn, None, "settings.video_model.changed", model, {"revision": expected_revision + 1, "platform": platform})
        return self.get()

    def key(self, model: str, delete: bool = False) -> dict:
        current = self.one(model)
        if not current["secret"]:
            raise DomainError("Lưu nền tảng và model ID trước khi nhập khóa.", 409)
        try:
            (delete_provider_key if delete else prompt_provider_key)(current["secret"])
        except RuntimeError as exc:
            raise DomainError(str(exc), 409) from exc
        except Exception as exc:
            raise DomainError("Không thao tác được với kho khóa Windows.", 409) from exc
        return self.get()
