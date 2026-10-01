CREATE TABLE video_grants (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), provider_kind TEXT NOT NULL,
    currency TEXT NOT NULL, max_micro INTEGER NOT NULL, reserved_micro INTEGER NOT NULL DEFAULT 0,
    spent_micro INTEGER NOT NULL DEFAULT 0, expires_at TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE video_reference_packages (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), shot_id TEXT NOT NULL REFERENCES shots(id),
    shot_revision INTEGER NOT NULL, version INTEGER NOT NULL, route TEXT NOT NULL,
    references_json TEXT NOT NULL, input_hash TEXT NOT NULL, validity TEXT NOT NULL DEFAULT 'CURRENT',
    created_at TEXT NOT NULL, UNIQUE(shot_id,version)
);
CREATE TABLE video_jobs (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), shot_id TEXT NOT NULL REFERENCES shots(id),
    shot_revision INTEGER NOT NULL, route TEXT NOT NULL, stage TEXT NOT NULL, provider_kind TEXT NOT NULL,
    operation_id TEXT NOT NULL UNIQUE, input_hash TEXT NOT NULL, request_json TEXT NOT NULL,
    state TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0, grant_id TEXT REFERENCES video_grants(id),
    estimated_micro INTEGER NOT NULL, actual_micro INTEGER, provider_request_id TEXT,
    error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX idx_video_jobs_state ON video_jobs(state,created_at);
CREATE TABLE video_attempts (
    id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES video_jobs(id), attempt INTEGER NOT NULL,
    state TEXT NOT NULL, provider_request_id TEXT, error TEXT, started_at TEXT NOT NULL, ended_at TEXT,
    UNIQUE(job_id,attempt)
);
CREATE TABLE video_takes (
    id TEXT PRIMARY KEY, job_id TEXT REFERENCES video_jobs(id), project_id TEXT NOT NULL REFERENCES projects(id),
    shot_id TEXT NOT NULL REFERENCES shots(id), shot_revision INTEGER NOT NULL, stage TEXT NOT NULL,
    route TEXT NOT NULL, version INTEGER NOT NULL, relative_path TEXT NOT NULL, sha256 TEXT NOT NULL,
    duration_frames INTEGER NOT NULL, fps_num INTEGER NOT NULL, fps_den INTEGER NOT NULL,
    provenance TEXT NOT NULL, provider_request_id TEXT, actual_micro INTEGER,
    review TEXT NOT NULL DEFAULT 'NEEDS_REVIEW', validity TEXT NOT NULL DEFAULT 'CURRENT',
    review_json TEXT, created_at TEXT NOT NULL, UNIQUE(shot_id,stage,version)
);
CREATE TABLE video_selections (
    shot_id TEXT NOT NULL REFERENCES shots(id), stage TEXT NOT NULL, take_id TEXT NOT NULL REFERENCES video_takes(id),
    revision INTEGER NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(shot_id,stage)
);
CREATE TABLE picture_locks (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), shot_id TEXT NOT NULL REFERENCES shots(id),
    version INTEGER NOT NULL, shot_revision INTEGER NOT NULL, final_take_id TEXT NOT NULL REFERENCES video_takes(id),
    edit_in INTEGER NOT NULL, edit_out_exclusive INTEGER NOT NULL, timing_version INTEGER,
    input_hash TEXT NOT NULL, validity TEXT NOT NULL DEFAULT 'CURRENT', created_at TEXT NOT NULL,
    UNIQUE(shot_id,version)
);
CREATE TABLE sound_stems (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), shot_id TEXT NOT NULL REFERENCES shots(id),
    picture_lock_id TEXT NOT NULL REFERENCES picture_locks(id), role TEXT NOT NULL, version INTEGER NOT NULL,
    relative_path TEXT NOT NULL, sha256 TEXT NOT NULL, sample_rate INTEGER NOT NULL,
    sample_count INTEGER NOT NULL, offset_frame INTEGER NOT NULL, review TEXT NOT NULL DEFAULT 'NEEDS_REVIEW',
    validity TEXT NOT NULL DEFAULT 'CURRENT', provenance TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(shot_id,role,version)
);
CREATE TABLE foley_jobs (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), shot_id TEXT NOT NULL REFERENCES shots(id),
    picture_lock_id TEXT NOT NULL REFERENCES picture_locks(id), operation_id TEXT NOT NULL UNIQUE,
    input_hash TEXT NOT NULL, source_take_id TEXT NOT NULL REFERENCES video_takes(id),
    state TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0, error TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX idx_foley_jobs_state ON foley_jobs(state,created_at);
CREATE TABLE delivery_packages (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
    version INTEGER NOT NULL, relative_path TEXT NOT NULL, input_hash TEXT NOT NULL,
    inventory_json TEXT NOT NULL, format TEXT NOT NULL, state TEXT NOT NULL,
    created_at TEXT NOT NULL, UNIQUE(episode_id,version)
);
