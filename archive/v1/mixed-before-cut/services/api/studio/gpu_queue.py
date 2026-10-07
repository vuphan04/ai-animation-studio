"""Single GPU arbiter shared by every queue that can load the GPU.

Each queue keeps its own bookkeeping; this lease is what stops UE, Kimodo and Foley work overlapping.
"""

from __future__ import annotations

from datetime import datetime, timezone


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


OWNER_KINDS = ("CACHE", "MRQ", "FOLEY", "MOTION", "KIMODO", "UNREAL", "SCAN", "JOB")


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
    counts = {"jobs": conn.execute("SELECT COUNT(*) FROM jobs WHERE state='QUEUED' AND resource='GPU_HEAVY'").fetchone()[0],
              "unreal": conn.execute("SELECT COUNT(*) FROM unreal_episode_jobs WHERE state='QUEUED'").fetchone()[0],
              "foley": conn.execute("SELECT (SELECT COUNT(*) FROM foley_jobs WHERE state='QUEUED')+(SELECT COUNT(*) FROM episode_foley_jobs WHERE state='QUEUED')").fetchone()[0],
              "scans": conn.execute("SELECT COUNT(*) FROM ue_asset_scans WHERE state='QUEUED'").fetchone()[0]}
    row = holder(conn)
    return {"lease": dict(row) if row else None, "waiting": counts}
