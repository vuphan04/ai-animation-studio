CREATE TABLE media_provider_settings (
    id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL,
    platform TEXT NOT NULL, model_id TEXT NOT NULL, updated_at TEXT NOT NULL
);
INSERT INTO media_provider_settings VALUES(1,1,'UNCONFIGURED','','');
CREATE TABLE foley_attempts (
    id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES foley_jobs(id), attempt INTEGER NOT NULL,
    state TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT, error TEXT,
    UNIQUE(job_id,attempt)
);
