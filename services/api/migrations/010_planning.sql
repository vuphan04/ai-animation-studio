CREATE TABLE screenplay_versions (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
    version INTEGER NOT NULL, language TEXT NOT NULL, target_language TEXT NOT NULL,
    text TEXT NOT NULL, sha256 TEXT NOT NULL, estimated_seconds INTEGER NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(episode_id,version)
);
CREATE TABLE planning_budget_grants (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), provider_kind TEXT NOT NULL,
    max_cost_microusd INTEGER NOT NULL, reserved_microusd INTEGER NOT NULL DEFAULT 0,
    spent_microusd INTEGER NOT NULL DEFAULT 0, expires_at TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE planning_jobs (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
    screenplay_id TEXT NOT NULL REFERENCES screenplay_versions(id), provider_kind TEXT NOT NULL,
    operation_id TEXT NOT NULL UNIQUE, input_hash TEXT NOT NULL, request_json TEXT NOT NULL,
    state TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL,
    estimated_cost_microusd INTEGER NOT NULL, grant_id TEXT REFERENCES planning_budget_grants(id),
    provider_request_id TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX idx_planning_jobs_state ON planning_jobs(state,created_at);
CREATE TABLE planning_attempts (
    id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES planning_jobs(id), attempt INTEGER NOT NULL,
    state TEXT NOT NULL, response_sha256 TEXT, validation_error TEXT, provider_request_id TEXT,
    started_at TEXT NOT NULL, ended_at TEXT, UNIQUE(job_id,attempt)
);
CREATE TABLE planning_drafts (
    id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE REFERENCES planning_jobs(id),
    project_id TEXT NOT NULL REFERENCES projects(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
    screenplay_id TEXT NOT NULL REFERENCES screenplay_versions(id), version INTEGER NOT NULL,
    draft_json TEXT NOT NULL, validation_json TEXT NOT NULL,
    provider_kind TEXT NOT NULL, estimated_cost_microusd INTEGER NOT NULL,
    actual_cost_microusd INTEGER, created_at TEXT NOT NULL, UNIQUE(screenplay_id,version)
);
CREATE TABLE planning_applications (
    id TEXT PRIMARY KEY, draft_id TEXT NOT NULL REFERENCES planning_drafts(id), shot_code TEXT NOT NULL,
    shot_id TEXT NOT NULL REFERENCES shots(id), shot_revision INTEGER NOT NULL,
    applied_by TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(draft_id,shot_code)
);
