"""P6 provider boundary. The production Seedance adapter stays disabled until a
versioned account, upload method, billing profile and live reconcile are proven.

The documented Ark model capabilities below are descriptive, not a live receipt.
"""

from dataclasses import dataclass
from typing import Protocol

from .domain import DomainError


@dataclass(frozen=True)
class VideoCapabilities:
    provider_kind: str
    model: str
    draft_resolutions: tuple[str, ...]
    final_resolutions: tuple[str, ...]
    first_last_frames: bool
    reference_video: bool
    draft_to_final: bool
    operational: bool
    currency: str
    source: str


SEEDANCE_25 = VideoCapabilities(
    provider_kind="SEEDANCE_ARK", model="Seedance 2.5 · profile tài liệu chưa kiểm chứng",
    # Regular generation supports 720p, but the selected draft_task upgrade
    # documented for 2.5 is 1080p only. Never silently turn it into a new 720p take.
    draft_resolutions=("480p",), final_resolutions=("1080p",),
    first_last_frames=True, reference_video=True, draft_to_final=True,
    operational=False, currency="CNY",
    source="https://docs.volcengine.com/docs/ark/create-video-generation-task-api?lang=zh",
)


@dataclass(frozen=True)
class VideoSubmitResult:
    provider_request_id: str


@dataclass(frozen=True)
class VideoPollResult:
    state: str  # PENDING, SUCCEEDED, FAILED
    relative_path: str | None = None
    sha256: str | None = None
    duration_frames: int | None = None
    fps_num: int | None = None
    fps_den: int | None = None
    actual_micro: int | None = None
    resolution: str | None = None


class VideoProvider(Protocol):
    capabilities: VideoCapabilities

    def estimate_micro(self, request: dict) -> int: ...
    def submit(self, request: dict) -> VideoSubmitResult: ...
    def poll(self, provider_request_id: str) -> VideoPollResult: ...


class DisabledSeedanceProvider:
    capabilities = SEEDANCE_25

    def estimate_micro(self, request: dict) -> int:
        raise DomainError("Chưa có bảng giá/account Seedance đã kiểm chứng; không thể cấp ngân sách request.", 409)

    def submit(self, request: dict) -> VideoSubmitResult:
        raise DomainError("Seedance live chưa được cấu hình và kiểm chứng; không gửi request có phí.", 409)

    def poll(self, provider_request_id: str) -> VideoPollResult:
        raise DomainError("Seedance live chưa có contract truy hồi đã kiểm chứng.", 409)
