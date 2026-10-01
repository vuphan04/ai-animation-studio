CREATE TABLE IF NOT EXISTS unreal_episode_attempts(
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES unreal_episode_jobs(id),
    attempt INTEGER NOT NULL,
    state TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    UNIQUE(job_id,attempt)
);
CREATE TABLE IF NOT EXISTS unreal_resource_leases(
    resource TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES unreal_episode_jobs(id),
    acquired_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS unreal_job_artifacts(
    job_id TEXT NOT NULL REFERENCES unreal_episode_jobs(id),
    artifact_id TEXT NOT NULL REFERENCES artifact_versions(id),
    PRIMARY KEY(job_id,artifact_id)
);
