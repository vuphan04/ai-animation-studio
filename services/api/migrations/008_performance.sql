CREATE TABLE voice_jobs (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), shot_id TEXT NOT NULL REFERENCES shots(id),
    shot_revision INTEGER NOT NULL, performance_id TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE,
    input_hash TEXT NOT NULL, request_json TEXT NOT NULL, state TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
    provider_request_id TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX idx_voice_jobs_state ON voice_jobs(state,created_at);
CREATE TABLE voice_job_attempts (
    id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES voice_jobs(id), attempt INTEGER NOT NULL,
    state TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT, UNIQUE(job_id,attempt)
);
CREATE TABLE voice_resource_lease (resource TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES voice_jobs(id), acquired_at TEXT NOT NULL);
CREATE TABLE voice_takes (
    id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE REFERENCES voice_jobs(id), project_id TEXT NOT NULL REFERENCES projects(id),
    shot_id TEXT NOT NULL REFERENCES shots(id), performance_id TEXT NOT NULL, version INTEGER NOT NULL,
    dialogue_version_id TEXT NOT NULL REFERENCES asset_versions(id), voice_profile_version_id TEXT NOT NULL REFERENCES asset_versions(id),
    provider_kind TEXT NOT NULL, provider_request_id TEXT NOT NULL, relative_path TEXT NOT NULL, wav_sha256 TEXT NOT NULL,
    sample_rate INTEGER NOT NULL, sample_count INTEGER NOT NULL, review TEXT NOT NULL DEFAULT 'NEEDS_REVIEW',
    lock_state TEXT NOT NULL DEFAULT 'UNLOCKED', validity TEXT NOT NULL DEFAULT 'CURRENT', created_at TEXT NOT NULL,
    UNIQUE(shot_id,performance_id,version)
);
CREATE TABLE voice_selections (
    shot_id TEXT NOT NULL REFERENCES shots(id), performance_id TEXT NOT NULL, take_id TEXT NOT NULL REFERENCES voice_takes(id),
    revision INTEGER NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(shot_id,performance_id)
);
CREATE TABLE timing_revisions (
    id TEXT PRIMARY KEY, shot_id TEXT NOT NULL REFERENCES shots(id), version INTEGER NOT NULL, shot_revision INTEGER NOT NULL,
    input_hash TEXT NOT NULL, placements_json TEXT NOT NULL, review TEXT NOT NULL DEFAULT 'NEEDS_REVIEW',
    validity TEXT NOT NULL DEFAULT 'CURRENT', created_at TEXT NOT NULL, UNIQUE(shot_id,version)
);
INSERT INTO subsystem_states(shot_id,subsystem,mode,lock_state,validity,review,state_revision)
SELECT id,'audio','AUTO','UNLOCKED','CURRENT','NEEDS_REVIEW',1 FROM shots;
