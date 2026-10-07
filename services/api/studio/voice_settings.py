"""ElevenLabs configuration and voice catalog. Listing voices and models creates no audio.

Saved or listed does not mean a speech request has succeeded; generation happens at C4 through the budget gate.
"""

from __future__ import annotations

import json

import httpx

from .domain import DomainError, canonical
from .secrets import delete_provider_key, get_provider_key, prompt_provider_key
from .service import now


PROVIDER = "ELEVENLABS"
BASE_URL = "https://api.elevenlabs.io"
# Raw PCM keeps the exact sample count, which is what shot timing is measured from.
OUTPUT_FORMATS = {"pcm_24000": 24000, "pcm_44100": 44100}


class VoiceSettings:
    def __init__(self, studio, transport=None, key_reader=None):
        self.studio = studio
        self.db = studio.db
        self.transport = transport
        self.key_reader = key_reader or (lambda: get_provider_key(PROVIDER))

    def _key(self) -> str | None:
        try:
            return self.key_reader()
        except Exception:
            return None

    def get(self) -> dict:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM voice_provider_settings WHERE id=1").fetchone()
            voices = conn.execute("SELECT COUNT(*),MAX(fetched_at) FROM voice_catalog WHERE provider=?", (PROVIDER,)).fetchone()
            models = [{"model_id": r["model_id"], "name": r["name"], "languages": json.loads(r["languages_json"])}
                      for r in conn.execute("SELECT * FROM voice_model_catalog WHERE provider=? ORDER BY name", (PROVIDER,))]
        value = {"revision": row["revision"] if row else 0, "provider": row["provider"] if row else "UNCONFIGURED",
                 "model_id": row["model_id"] if row else "", "output_format": row["output_format"] if row else "pcm_24000"}
        key = bool(self._key())
        return {**value, "key_configured": key, "configured": value["provider"] == PROVIDER and key,
                "voice_count": voices[0], "catalog_fetched_at": voices[1], "models": models, "integration_verified": False,
                "output_formats": [{"id": key, "sample_rate": rate} for key, rate in OUTPUT_FORMATS.items()]}

    def save(self, expected_revision: int, provider: str, model_id: str, output_format: str = "pcm_24000") -> dict:
        if provider not in ("UNCONFIGURED", PROVIDER) or not isinstance(model_id, str) or output_format not in OUTPUT_FORMATS:
            raise DomainError("Nhà cung cấp giọng hoặc định dạng âm thanh không hợp lệ.")
        with self.db.transaction() as conn:
            row = conn.execute("SELECT revision FROM voice_provider_settings WHERE id=1").fetchone()
            if (row["revision"] if row else 0) != expected_revision:
                raise DomainError("Cấu hình giọng đã đổi ở phiên khác; tải lại trước khi lưu.", 409)
            if model_id and not conn.execute("SELECT 1 FROM voice_model_catalog WHERE provider=? AND model_id=?", (PROVIDER, model_id)).fetchone():
                raise DomainError("Chọn model trong danh sách đã tải từ ElevenLabs; không nhập ID tự do.", 409)
            conn.execute("INSERT OR REPLACE INTO voice_provider_settings(id,revision,provider,model_id,output_format,updated_at) VALUES(1,?,?,?,?,?)",
                         (expected_revision + 1, provider, model_id if provider == PROVIDER else "", output_format, now()))
            self.studio.audit(conn, None, "settings.voice.changed", "voice", {"revision": expected_revision + 1, "provider": provider})
        return self.get()

    def key(self, delete: bool = False) -> dict:
        try:
            (delete_provider_key if delete else prompt_provider_key)(PROVIDER)
        except RuntimeError as exc:
            raise DomainError(str(exc), 409) from exc
        except Exception as exc:
            raise DomainError("Không thao tác được với kho khóa Windows.", 409) from exc
        return self.get()

    def refresh_catalog(self) -> dict:
        key = self._key()
        if not key:
            raise DomainError("Chưa có khóa ElevenLabs trong kho bảo mật Windows.", 409)
        try:
            with httpx.Client(transport=self.transport, timeout=30, trust_env=False) as client:
                voices = client.get(BASE_URL + "/v1/voices", headers={"xi-api-key": key})
                models = client.get(BASE_URL + "/v1/models", headers={"xi-api-key": key})
        except httpx.HTTPError as exc:
            raise DomainError("Không kết nối được ElevenLabs; kiểm tra mạng và thử lại.", 409) from exc
        for response in (voices, models):
            if response.status_code != 200:
                raise DomainError(f"ElevenLabs trả HTTP {response.status_code}; kiểm tra khóa và quyền tài khoản.", 409)
        try:
            voice_rows = [(PROVIDER, v["voice_id"], v["name"], v.get("category"), canonical(v.get("labels") or {}), v.get("preview_url"))
                          for v in voices.json()["voices"]]
            model_rows = [(PROVIDER, m["model_id"], m["name"], canonical([l.get("language_id") for l in m.get("languages") or []]))
                          for m in models.json() if m.get("can_do_text_to_speech")]
            if len(voice_rows) > 5000 or any(not isinstance(r[1], str) or not r[1] or not isinstance(r[2], str) for r in voice_rows + model_rows):
                raise ValueError("unexpected catalog shape")
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise DomainError("ElevenLabs trả dữ liệu danh sách không đúng dạng mong đợi; không lưu.", 409) from exc
        stamp = now()
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM voice_catalog WHERE provider=?", (PROVIDER,))
            conn.execute("DELETE FROM voice_model_catalog WHERE provider=?", (PROVIDER,))
            conn.executemany("INSERT OR REPLACE INTO voice_catalog(provider,voice_id,name,category,labels_json,preview_url,fetched_at) VALUES(?,?,?,?,?,?,?)",
                             [(*row, stamp) for row in voice_rows])
            conn.executemany("INSERT OR REPLACE INTO voice_model_catalog(provider,model_id,name,languages_json,fetched_at) VALUES(?,?,?,?,?)",
                             [(*row, stamp) for row in model_rows])
            self.studio.audit(conn, None, "settings.voice.catalog", "voice", {"voices": len(voice_rows), "models": len(model_rows)})
        return self.get()

    def voices(self) -> list[dict]:
        with self.db.connect() as conn:
            return [{"voice_id": r["voice_id"], "name": r["name"], "category": r["category"],
                     "labels": json.loads(r["labels_json"]), "preview_url": r["preview_url"]}
                    for r in conn.execute("SELECT * FROM voice_catalog WHERE provider=? ORDER BY name", (PROVIDER,))]
