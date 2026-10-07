"""Save operator choices without granting unsupported cloud capabilities."""
from dataclasses import replace
import re

from .domain import DomainError
from .secrets import delete_provider_key, get_provider_key, prompt_provider_key
from .service import now
from .video_adapter import SEEDANCE_25, DisabledSeedanceProvider


class MediaSettings:
    def __init__(self, studio):
        self.db = studio.db

    def get(self):
        with self.db.connect() as conn:
            result = dict(conn.execute("SELECT revision,platform,model_id,updated_at FROM media_provider_settings WHERE id=1").fetchone())
        provider = "SEEDANCE_ARK" if result["platform"] == "VOLCENGINE" else "SEEDANCE_BYTEPLUS"
        try:
            has_key = result["platform"] != "UNCONFIGURED" and bool(get_provider_key(provider))
        except Exception:
            has_key = False
        return dict(result, key_configured=has_key, configured=bool(has_key and result["model_id"]),
                    operational=False, integration_verified=False,
                    blockers=["Chưa có profile upload, giá, model capability và live reconcile đã kiểm chứng. Lưu cấu hình không mở API có phí.",
                              "Seedance 2.5: nâng draft_task lên final chỉ hỗ trợ 1080p; preset final 720p cần một profile khác được duyệt.",
                              "First/last-frame và omni references là các chế độ không thể trộn; cần duyệt phương án continuity phù hợp."])

    def save(self, expected_revision, platform, model_id):
        if platform not in ("UNCONFIGURED", "BYTEPLUS", "VOLCENGINE") or not isinstance(model_id, str) or (model_id and not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,128}", model_id)) or (platform != "UNCONFIGURED" and not model_id):
            raise DomainError("Chọn nền tảng và model/endpoint ID từ tài khoản provider; không nhập URL, khóa hoặc giá trị đoán.")
        with self.db.transaction() as conn:
            revision = conn.execute("SELECT revision FROM media_provider_settings WHERE id=1").fetchone()[0]
            if expected_revision != revision:
                raise DomainError("Cấu hình video đã đổi; tải lại trước khi lưu.", 409)
            conn.execute("UPDATE media_provider_settings SET revision=revision+1,platform=?,model_id=?,updated_at=? WHERE id=1", (platform, model_id, now()))
        return self.get()

    def key(self, delete=False):
        settings = self.get()
        if settings["platform"] == "UNCONFIGURED":
            raise DomainError("Lưu nền tảng và model ID trước khi nhập khóa.", 409)
        provider = "SEEDANCE_ARK" if settings["platform"] == "VOLCENGINE" else "SEEDANCE_BYTEPLUS"
        try:
            (delete_provider_key if delete else prompt_provider_key)(provider)
        except RuntimeError as exc:
            raise DomainError(str(exc), 409) from exc
        except Exception as exc:
            raise DomainError("Không thao tác được với kho khóa Windows.", 409) from exc
        with self.db.transaction() as conn:
            conn.execute("UPDATE media_provider_settings SET revision=revision+1,updated_at=? WHERE id=1", (now(),))
        return self.get()

    def disabled_provider(self):
        settings = self.get()
        platform = settings["platform"]
        provider = DisabledSeedanceProvider()
        provider.capabilities = replace(SEEDANCE_25, provider_kind={"VOLCENGINE": "SEEDANCE_ARK", "BYTEPLUS": "SEEDANCE_BYTEPLUS"}.get(platform, "SEEDANCE_UNCONFIGURED"),
                                        model=settings["model_id"] or "Chưa chọn model Seedance", currency="CNY" if platform == "VOLCENGINE" else "USD",
                                        draft_resolutions=(), final_resolutions=(), first_last_frames=False, reference_video=False, draft_to_final=False,
                                        source="https://docs.byteplus.com/en/docs/modelark/create-video-generation-task-api" if platform == "BYTEPLUS" else SEEDANCE_25.source)
        return provider
