-- Workflow V4.5 zone C: finals (10), conform (11) and the technical review of finals (C8).
CREATE TABLE final_jobs (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
    kind TEXT NOT NULL CHECK(kind IN ('UE_RENDER','AI_FINAL')), shot_id TEXT, provider_kind TEXT, model TEXT,
    operation_id TEXT NOT NULL UNIQUE, input_hash TEXT NOT NULL, request_json TEXT NOT NULL, state TEXT NOT NULL, stage TEXT NOT NULL,
    provider_task_id TEXT, estimated_micro INTEGER NOT NULL DEFAULT 0, actual_micro INTEGER, currency TEXT,
    error TEXT, result_json TEXT, poll_after TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX idx_final_jobs_state ON final_jobs(state,kind,created_at);
-- What step 10 produced for a shot, before conform: Unreal's final frames as one clip, or the provider's final video.
CREATE TABLE final_takes (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
    shot_id TEXT NOT NULL, version INTEGER NOT NULL, job_id TEXT, provenance TEXT NOT NULL, route TEXT NOT NULL, model TEXT,
    provider_task_id TEXT, draft_take_id TEXT, basis_hash TEXT NOT NULL, relative_path TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL,
    frames INTEGER NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL, fps_num INTEGER NOT NULL, fps_den INTEGER NOT NULL,
    audio_streams INTEGER NOT NULL, created_at TEXT NOT NULL, UNIQUE(episode_id,shot_id,version)
);
-- Step 11: the master of a shot at the series standard, plus a browser-playable review copy.
CREATE TABLE conformed_shots (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
    shot_id TEXT NOT NULL, version INTEGER NOT NULL, final_take_id TEXT NOT NULL REFERENCES final_takes(id), spec_hash TEXT NOT NULL,
    relative_path TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, review_path TEXT NOT NULL, qc_json TEXT NOT NULL,
    created_at TEXT NOT NULL, UNIQUE(episode_id,shot_id,version)
);
CREATE TABLE final_approvals (
    episode_id TEXT NOT NULL REFERENCES episodes(id), shot_id TEXT NOT NULL, project_id TEXT NOT NULL REFERENCES projects(id),
    c8_hash TEXT NOT NULL, checks_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(episode_id,shot_id)
);
