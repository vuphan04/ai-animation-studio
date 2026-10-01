CREATE TABLE unreal_episode_jobs(
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    episode_id TEXT NOT NULL REFERENCES episodes(id),
    operation_id TEXT NOT NULL UNIQUE,
    input_hash TEXT NOT NULL,
    operation_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('QUEUED','RUNNING','BLOCKED','RECONCILING','COMPLETED')),
    attempt INTEGER NOT NULL DEFAULT 0,
    result_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_unreal_episode_jobs_state ON unreal_episode_jobs(state,created_at);
CREATE TABLE unreal_episode_attempts(
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES unreal_episode_jobs(id),
    attempt INTEGER NOT NULL,
    state TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    UNIQUE(job_id,attempt)
);
CREATE TABLE unreal_resource_leases(
    resource TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES unreal_episode_jobs(id),
    acquired_at TEXT NOT NULL
);
CREATE TABLE unreal_job_artifacts(
    job_id TEXT NOT NULL REFERENCES unreal_episode_jobs(id),
    artifact_id TEXT NOT NULL REFERENCES artifact_versions(id),
    PRIMARY KEY(job_id,artifact_id)
);
