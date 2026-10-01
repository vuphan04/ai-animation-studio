"""P1-only fake adapter. It never calls Unreal, cloud providers or Resolve."""

from __future__ import annotations


class FakeAdapter:
    kind = "FAKE_CONTROL_FLOW"

    def apply(self, job: dict) -> dict:
        return {
            "adapter_kind": self.kind,
            "operation_id": job["operation_id"],
            "input_hash": job["input_hash"],
            "shot_id": job["shot_id"],
            "shot_revision": job["shot_revision"],
            "subsystem": job["subsystem"],
            "artifact_role": job["subsystem"],
            "external_writes": 0,
        }
