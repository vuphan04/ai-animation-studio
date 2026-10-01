ALTER TABLE recovery_jobs ADD COLUMN operation_id TEXT;
ALTER TABLE recovery_jobs ADD COLUMN input_hash TEXT;
ALTER TABLE recovery_jobs ADD COLUMN attempt INTEGER NOT NULL DEFAULT 0;
ALTER TABLE recovery_jobs ADD COLUMN provider_request_id TEXT;
CREATE UNIQUE INDEX recovery_operation_unique ON recovery_jobs(operation_id);
CREATE TABLE recovery_attempts (
    id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES recovery_jobs(id), attempt INTEGER NOT NULL,
    state TEXT NOT NULL, receipt_id TEXT REFERENCES receipts(id), started_at TEXT NOT NULL,
    ended_at TEXT, UNIQUE(job_id,attempt)
);
CREATE TABLE recovery_dependencies (
    job_id TEXT NOT NULL REFERENCES recovery_jobs(id), depends_on_id TEXT NOT NULL REFERENCES recovery_jobs(id),
    PRIMARY KEY(job_id,depends_on_id), CHECK(job_id != depends_on_id)
);
