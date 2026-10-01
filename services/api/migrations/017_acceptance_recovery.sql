CREATE TABLE acceptance_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    episode_id TEXT NOT NULL REFERENCES episodes(id),
    evidence_kind TEXT NOT NULL CHECK(evidence_kind IN ('LOCAL_CONTRACT','REAL_INTEGRATION')),
    snapshot_json TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE acceptance_records (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES acceptance_runs(id),
    version INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('CASE','MEASUREMENTS','HUMAN_REVIEW')),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id,version)
);
CREATE TABLE recovery_settings (
    id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL,
    backup_parent TEXT NOT NULL, restore_parent TEXT NOT NULL
);
CREATE TABLE recovery_jobs (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('BACKUP','RESTORE','EVIDENCE')),
    state TEXT NOT NULL, request_json TEXT NOT NULL, result_json TEXT,
    error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE job_cancellations (
    job_id TEXT PRIMARY KEY REFERENCES jobs(id), requested_at TEXT NOT NULL
);
