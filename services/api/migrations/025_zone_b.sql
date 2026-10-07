-- Workflow V4.5 zone B: UE and Hybrid-source branch (07, 08-b1..b6, C5) and AI branch (07, 08, C5, 09/C6).
-- One row per character action inside a shot; a slot is what an animation gets assigned to (08-b1..b3).
CREATE TABLE animation_slots (
    episode_id TEXT NOT NULL REFERENCES episodes(id), slot_id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id), shot_id TEXT NOT NULL, character_key TEXT NOT NULL,
    position INTEGER NOT NULL, tag TEXT NOT NULL, start_frame INTEGER NOT NULL, end_frame INTEGER NOT NULL,
    basis_hash TEXT NOT NULL, clips_json TEXT NOT NULL, upper_body_path TEXT, source TEXT NOT NULL,
    confidence REAL NOT NULL, status TEXT NOT NULL CHECK(status IN ('ASSIGNED','MISSING','KIMODO')),
    approved INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY(episode_id,slot_id)
);
-- Motion that the library lacks, to be generated with Kimodo outside the app (08-b4) and reviewed here (08-b5).
CREATE TABLE kimodo_requests (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
    slot_id TEXT NOT NULL, prompt TEXT NOT NULL, seconds REAL NOT NULL, variants INTEGER NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('PENDING','RESULTS_ATTACHED','APPROVED')), variants_json TEXT NOT NULL,
    selected_path TEXT, revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(episode_id,slot_id)
);
-- Clips the operator approved for reuse across episodes, with the tags they answer to.
CREATE TABLE series_motion_clips (
    project_id TEXT NOT NULL REFERENCES projects(id), asset_path TEXT NOT NULL, tags_json TEXT NOT NULL,
    source TEXT NOT NULL, origin_slot_id TEXT, created_at TEXT NOT NULL, PRIMARY KEY(project_id,asset_path)
);
CREATE TABLE episode_ue_jobs (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
    kind TEXT NOT NULL CHECK(kind IN ('ASSEMBLE','ANIMATE')), operation_id TEXT NOT NULL UNIQUE, input_hash TEXT NOT NULL,
    request_json TEXT NOT NULL, state TEXT NOT NULL, result_json TEXT, error TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
-- What Unreal last reported for a shot, and the hash the operator locked at C5.
CREATE TABLE ue_shot_states (
    episode_id TEXT NOT NULL REFERENCES episodes(id), shot_id TEXT NOT NULL, project_id TEXT NOT NULL REFERENCES projects(id),
    sequence_path TEXT, assembled_hash TEXT, animated_hash TEXT, build_notes_json TEXT,
    c5_hash TEXT, c5_notes_json TEXT, updated_at TEXT NOT NULL, PRIMARY KEY(episode_id,shot_id)
);
CREATE TABLE video_model_settings (
    model TEXT PRIMARY KEY, platform TEXT NOT NULL, model_id TEXT NOT NULL, revision INTEGER NOT NULL,
    verified_at TEXT, updated_at TEXT NOT NULL
);
-- Operator preparation of an AI or Hybrid shot (07): prompt, which Style bible references to send, the locked draft.
CREATE TABLE ai_shot_inputs (
    episode_id TEXT NOT NULL REFERENCES episodes(id), shot_id TEXT NOT NULL, project_id TEXT NOT NULL REFERENCES projects(id),
    prompt_override TEXT, reference_keys_json TEXT, c6_take_id TEXT, revision INTEGER NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY(episode_id,shot_id)
);
-- UE-rendered source of a Hybrid shot that steers the AI draft: a control video or a first frame.
CREATE TABLE hybrid_control_inputs (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
    shot_id TEXT NOT NULL, version INTEGER NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('VIDEO','FIRST_FRAME')),
    relative_path TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(episode_id,shot_id,version)
);
CREATE TABLE ai_video_jobs (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
    shot_id TEXT NOT NULL, stage TEXT NOT NULL, provider_kind TEXT NOT NULL, model TEXT NOT NULL,
    operation_id TEXT NOT NULL UNIQUE, input_hash TEXT NOT NULL, request_json TEXT NOT NULL, state TEXT NOT NULL,
    provider_task_id TEXT, estimated_micro INTEGER NOT NULL, actual_micro INTEGER, currency TEXT NOT NULL,
    take_id TEXT, error TEXT, poll_after TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX idx_ai_video_jobs_state ON ai_video_jobs(state,created_at);
CREATE TABLE ai_video_takes (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
    shot_id TEXT NOT NULL, version INTEGER NOT NULL, job_id TEXT NOT NULL REFERENCES ai_video_jobs(id),
    stage TEXT NOT NULL, route TEXT NOT NULL, provider_kind TEXT NOT NULL, model TEXT NOT NULL,
    provider_task_id TEXT, expires_at TEXT, content_hash TEXT NOT NULL, prompt TEXT NOT NULL,
    requested_seconds INTEGER NOT NULL, measured_seconds REAL, relative_path TEXT NOT NULL, sha256 TEXT NOT NULL,
    bytes INTEGER NOT NULL, review TEXT NOT NULL CHECK(review IN ('NEEDS_REVIEW','APPROVED','REJECTED')),
    checks_json TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(episode_id,shot_id,version)
);
