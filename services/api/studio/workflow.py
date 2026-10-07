"""The checkpoints of the workflow (CS1, CS2, C1–C10) and the record of each approval, tied to the exact version approved."""

from __future__ import annotations

import json

from .domain import DomainError, canonical
from .service import now, uid


CHECKPOINTS = (
    {"key": "CS1", "name": "series_mapping", "zone": "0", "scope_kind": "SERIES", "label": "Chọn mapping từ danh sách asset", "implemented": True},
    {"key": "CS2", "name": "style_bible", "zone": "0", "scope_kind": "SERIES", "label": "Duyệt Style bible", "implemented": True},
    {"key": "C1", "name": "breakdown", "zone": "A", "scope_kind": "EPISODE", "label": "Chốt breakdown", "implemented": True},
    {"key": "C2", "name": "library_supplement", "zone": "A", "scope_kind": "EPISODE", "label": "Duyệt phần bổ sung thư viện", "implemented": True},
    # C3 and C4 are recorded once per episode; the per-shot locks and per-line takes live in their own tables.
    {"key": "C3", "name": "route", "zone": "A", "scope_kind": "EPISODE", "label": "Phân loại shot và chi phí", "implemented": True},
    {"key": "C4", "name": "voice_timing", "zone": "A", "scope_kind": "EPISODE", "label": "Duyệt thoại và timing", "implemented": True},
    {"key": "C5", "name": "performance", "zone": "B", "scope_kind": "SHOT", "label": "Chốt chuyển động / draft / nguồn UE", "implemented": True},
    {"key": "C6", "name": "pre_rough_cut", "zone": "B", "scope_kind": "SHOT", "label": "Cache, proxy và draft cho rough cut", "implemented": True},
    {"key": "C7", "name": "picture_lock", "zone": "B", "scope_kind": "EPISODE", "label": "Rough cut và khóa dựng", "implemented": True},
    {"key": "C8", "name": "final_technical", "zone": "C", "scope_kind": "SHOT", "label": "Duyệt final kỹ thuật", "implemented": True},
    {"key": "C9", "name": "sound", "zone": "C", "scope_kind": "EPISODE", "label": "Foley và âm thanh", "implemented": True},
    {"key": "C10", "name": "delivery", "zone": "C", "scope_kind": "EPISODE", "label": "Import DaVinci Resolve và xuất phim", "implemented": True},
)
BY_KEY = {item["key"]: item for item in CHECKPOINTS}


def record_checkpoint(studio, conn, project_id: str, key: str, scope_id: str, exact_version: int,
                      dependency_hash: str, decision: dict) -> dict:
    definition = BY_KEY.get(key)
    if not definition or not definition["implemented"]:
        raise DomainError("Checkpoint này chưa được triển khai; không ghi quyết định duyệt.", 409)
    item = {"id": uid(), "project_id": project_id, "checkpoint": key, "scope_kind": definition["scope_kind"],
            "scope_id": scope_id, "exact_version": exact_version, "dependency_hash": dependency_hash,
            "decision_json": canonical(decision), "actor": "local_user", "created_at": now()}
    conn.execute("INSERT INTO workflow_checkpoints(id,project_id,checkpoint,scope_kind,scope_id,exact_version,dependency_hash,decision_json,actor,created_at) VALUES(:id,:project_id,:checkpoint,:scope_kind,:scope_id,:exact_version,:dependency_hash,:decision_json,:actor,:created_at)", item)
    studio.audit(conn, project_id, "workflow.checkpoint", scope_id, {"checkpoint": key, "version": exact_version})
    return item


def status(studio, project_id: str) -> dict:
    """The checkpoints of the workflow with the latest approval of each series-level one."""
    studio.project(project_id)
    with studio.db.connect() as conn:
        rows = conn.execute("SELECT * FROM workflow_checkpoints WHERE project_id=? ORDER BY created_at", (project_id,)).fetchall()
    latest: dict[str, dict] = {}
    for row in rows:
        if row["scope_kind"] == "SERIES":
            latest[row["checkpoint"]] = {"exact_version": row["exact_version"], "dependency_hash": row["dependency_hash"],
                                         "decision": json.loads(row["decision_json"]), "actor": row["actor"],
                                         "created_at": row["created_at"]}
    return {"checkpoints": [{**item, "latest_series_approval": latest.get(item["key"])} for item in CHECKPOINTS]}
