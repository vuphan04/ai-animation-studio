"""Foley worker contract. No local model is selected or benchmarked in P0."""

from dataclasses import dataclass
from typing import Callable, Protocol

from .domain import DomainError


@dataclass(frozen=True)
class FoleyOutput:
    relative_path: str
    sha256: str
    provenance: str


class FoleyWorker(Protocol):
    kind: str
    operational: bool

    def generate(self, request: dict, *, before_write: Callable[[], None]) -> FoleyOutput: ...


class DisabledFoleyWorker:
    kind = "FOLEY_UNCONFIGURED"
    operational = False

    def generate(self, request: dict, *, before_write: Callable[[], None]) -> FoleyOutput:
        raise DomainError("P0 chưa chọn hoặc đo Foley model/runtime; không tạo âm thanh.", 409)
