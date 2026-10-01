"""Operator-facing AI configuration. Secret values never enter HTTP or SQLite."""

from __future__ import annotations

from .domain import DomainError
from .secrets import delete_provider_key, get_provider_key, prompt_provider_key
from .service import now
from .model_catalog import MODEL_CATALOG, public_models


MODELS = set(MODEL_CATALOG)
PROVIDERS = {"LOCAL_PLANNER_FIXTURE", "OPENAI"}


class AISettings:
    def __init__(self, studio):
        self.db = studio.db

    def _key_configured(self) -> bool:
        try:
            return bool(get_provider_key("OPENAI"))
        except Exception:
            return False

    def get(self) -> dict:
        with self.db.connect() as conn:
            value = dict(conn.execute("SELECT revision,provider_kind,model,max_output_tokens,updated_at FROM ai_settings WHERE id=1").fetchone())
        value["key_configured"] = self._key_configured()
        value["configured"] = value["provider_kind"] == "LOCAL_PLANNER_FIXTURE" or value["key_configured"]
        value["available_models"] = public_models()
        return value

    def save(self, provider_kind: str, model: str, max_output_tokens: int, expected_revision: int) -> dict:
        if provider_kind not in PROVIDERS or model not in MODELS or type(max_output_tokens) is not int or not 512 <= max_output_tokens <= 16384:
            raise DomainError("Provider, model hoặc giới hạn output không hợp lệ.")
        with self.db.transaction() as conn:
            current = conn.execute("SELECT revision FROM ai_settings WHERE id=1").fetchone()[0]
            if current != expected_revision:
                raise DomainError("Cấu hình AI đã đổi ở phiên khác; tải lại trước khi lưu.", 409)
            conn.execute("UPDATE ai_settings SET revision=?,provider_kind=?,model=?,max_output_tokens=?,updated_at=? WHERE id=1",
                         (current + 1, provider_kind, model, max_output_tokens, now()))
        return self.get()

    def prompt_key(self) -> dict:
        try:
            prompt_provider_key("OPENAI")
        except RuntimeError as exc:
            raise DomainError(str(exc), 409) from exc
        except Exception as exc:
            raise DomainError("Không lưu được khóa vào Windows Credential Manager.", 409) from exc
        with self.db.transaction() as conn:
            conn.execute("UPDATE ai_settings SET revision=revision+1,updated_at=? WHERE id=1", (now(),))
        return self.get()

    def delete_key(self) -> dict:
        try:
            delete_provider_key("OPENAI")
        except Exception as exc:
            raise DomainError("Kho khóa Windows không khả dụng trên máy này.", 409) from exc
        with self.db.transaction() as conn:
            conn.execute("UPDATE ai_settings SET revision=revision+1,updated_at=? WHERE id=1", (now(),))
        return self.get()
