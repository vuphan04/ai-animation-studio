"""Workflow V4.5 checkpoint definitions and exact-version approvals.

V1 approvals (scope 'plan', 'subsystem:*') are a different record and are never read as V4.5 checkpoints.
"""

from __future__ import annotations

import json

from .domain import DomainError, canonical
from .service import now, uid


WORKFLOW_V1 = "V1"
WORKFLOW_V45 = "V4_5"
# `implemented` marks checkpoints the app can actually record today; the rest are shown as upcoming, not approvable.
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
    project = conn.execute("SELECT workflow_version FROM projects WHERE id=?", (project_id,)).fetchone()
    if project is None or project["workflow_version"] != WORKFLOW_V45:
        raise DomainError("Dự án dùng workflow V1; checkpoint V4.5 chỉ áp dụng cho dự án V4.5.", 409)
    item = {"id": uid(), "project_id": project_id, "checkpoint": key, "scope_kind": definition["scope_kind"],
            "scope_id": scope_id, "exact_version": exact_version, "dependency_hash": dependency_hash,
            "decision_json": canonical(decision), "actor": "local_user", "created_at": now()}
    conn.execute("INSERT INTO workflow_checkpoints(id,project_id,checkpoint,scope_kind,scope_id,exact_version,dependency_hash,decision_json,actor,created_at) VALUES(:id,:project_id,:checkpoint,:scope_kind,:scope_id,:exact_version,:dependency_hash,:decision_json,:actor,:created_at)", item)
    studio.audit(conn, project_id, "workflow.checkpoint", scope_id, {"checkpoint": key, "version": exact_version})
    return item


def require_production_ready(studio, conn, project_id: str, episode_id: str) -> None:
    """V4.5 builds nothing for an episode (UE sequence, bake/cache, video request) before its C3 and C4 are current."""
    project = conn.execute("SELECT workflow_version FROM projects WHERE id=?", (project_id,)).fetchone()
    if project is None or project["workflow_version"] != WORKFLOW_V45:
        return
    gate = getattr(studio, "production_gate", None)
    blockers = gate(conn, episode_id) if gate else ["Cổng C3/C4 của workflow V4.5 chưa được nạp."]
    if blockers:
        raise DomainError("Chưa được dựng hoặc gửi yêu cầu video cho tập này: " + " ".join(blockers), 409)


def upgrade(studio, project_id: str, confirmed: bool) -> dict:
    """Move a V1 project onto V4.5. Old approvals, outputs and receipts stay as history and grant nothing new."""
    if not confirmed:
        raise DomainError("Xác nhận đã hiểu: approval V1 không được tính là checkpoint V4.5.")
    with studio.db.transaction() as conn:
        project = conn.execute("SELECT workflow_version FROM projects WHERE id=?", (project_id,)).fetchone()
        if project is None:
            raise DomainError("project not found", 404)
        if project["workflow_version"] != WORKFLOW_V45:
            conn.execute("UPDATE projects SET workflow_version=? WHERE id=?", (WORKFLOW_V45, project_id))
            studio.audit(conn, project_id, "workflow.upgraded", project_id, {"from": project["workflow_version"], "to": WORKFLOW_V45})
    return status(studio, project_id)


def status(studio, project_id: str) -> dict:
    project = studio.project(project_id)
    with studio.db.connect() as conn:
        rows = conn.execute("SELECT * FROM workflow_checkpoints WHERE project_id=? ORDER BY created_at", (project_id,)).fetchall()
    latest: dict[str, dict] = {}
    for row in rows:
        if row["scope_kind"] == "SERIES":
            latest[row["checkpoint"]] = {"exact_version": row["exact_version"], "dependency_hash": row["dependency_hash"],
                                         "decision": json.loads(row["decision_json"]), "actor": row["actor"],
                                         "created_at": row["created_at"]}
    return {"workflow_version": project["workflow_version"],
            "checkpoints": [{**item, "latest_series_approval": latest.get(item["key"])} for item in CHECKPOINTS]}
