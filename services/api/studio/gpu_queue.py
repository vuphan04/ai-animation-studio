"""Single GPU arbiter shared by every queue that can load the GPU.

Each queue keeps its own bookkeeping; this lease is what stops UE, Kimodo and Foley work overlapping.
"""

from __future__ import annotations

from datetime import datetime, timezone


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


OWNER_KINDS = ("MRQ", "FOLEY", "KIMODO", "UNREAL", "SCAN")


def holder(conn):
    return conn.execute("SELECT * FROM gpu_heavy_leases WHERE resource='GPU_HEAVY'").fetchone()


def try_acquire(conn, operation_id: str, owner_kind: str) -> bool:
    if owner_kind not in OWNER_KINDS:
        raise ValueError("unknown GPU owner kind")
    row = holder(conn)
    if row:
        return row["operation_id"] == operation_id and row["state"] == "ACTIVE"
    conn.execute("INSERT INTO gpu_heavy_leases(resource,operation_id,owner_kind,heartbeat_at,state) VALUES('GPU_HEAVY',?,?,?,'ACTIVE')",
                 (operation_id, owner_kind, now()))
    return True


def release(conn, operation_id: str) -> None:
    conn.execute("DELETE FROM gpu_heavy_leases WHERE resource='GPU_HEAVY' AND operation_id=?", (operation_id,))


def waiting(conn) -> dict:
    count = lambda table, extra="": conn.execute(f"SELECT COUNT(*) FROM {table} WHERE state='QUEUED'{extra}").fetchone()[0]
    counts = {"unreal": count("episode_ue_jobs"), "renders": count("ue_proxy_jobs") + count("final_jobs", " AND kind='UE_RENDER'"),
              "scans": count("ue_asset_scans") + count("style_capture_jobs"), "foley": count("episode_foley_jobs")}
    row = holder(conn)
    return {"lease": dict(row) if row else None, "waiting": counts}
