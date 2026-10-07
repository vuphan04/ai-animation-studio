-- Workflow V4.5 zone A: shot routes and cost (05/C3), ElevenLabs dialogue and timing (06/C4).
-- The V1 tables voice_jobs/voice_takes/timing_revisions stay as they are; nothing here reads them.
ALTER TABLE voice_provider_settings ADD COLUMN output_format TEXT NOT NULL DEFAULT 'pcm_24000';
-- Series-wide inputs of the C3 rules: who counts as a main character and what each provider charges.
CREATE TABLE route_settings (
    project_id TEXT PRIMARY KEY REFERENCES projects(id), revision INTEGER NOT NULL,
    main_characters_json TEXT NOT NULL, prices_json TEXT NOT NULL, updated_at TEXT NOT NULL
);
-- One row per breakdown shot ID. A lock only counts while basis_hash still matches the facts of that shot.
CREATE TABLE route_decisions (
    episode_id TEXT NOT NULL REFERENCES episodes(id), shot_id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id),
    route TEXT NOT NULL CHECK(route IN ('UE','AI','HYBRID')), video_model TEXT,
    locked INTEGER NOT NULL DEFAULT 0, basis_hash TEXT NOT NULL, revision INTEGER NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY(episode_id,shot_id)
);
CREATE TABLE episode_voice_settings (
    episode_id TEXT PRIMARY KEY REFERENCES episodes(id), project_id TEXT NOT NULL REFERENCES projects(id),
    language TEXT NOT NULL CHECK(language IN ('ORIGINAL','EN')), revision INTEGER NOT NULL, updated_at TEXT NOT NULL
);
-- Operator direction for one line: what is read aloud, how, and which take is in use.
CREATE TABLE dialogue_lines (
    episode_id TEXT NOT NULL REFERENCES episodes(id), line_id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id),
    spoken_text TEXT, settings_json TEXT NOT NULL, selected_take_id TEXT, revision INTEGER NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY(episode_id,line_id)
);
CREATE TABLE dialogue_jobs (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
    line_id TEXT NOT NULL, provider_kind TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE, input_hash TEXT NOT NULL,
    request_json TEXT NOT NULL, state TEXT NOT NULL, estimated_micro INTEGER NOT NULL, actual_micro INTEGER,
    currency TEXT NOT NULL, provider_request_id TEXT, take_id TEXT, error TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX idx_dialogue_jobs_state ON dialogue_jobs(state,created_at);
CREATE TABLE dialogue_takes (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
    line_id TEXT NOT NULL, version INTEGER NOT NULL, job_id TEXT NOT NULL REFERENCES dialogue_jobs(id),
    provider_kind TEXT NOT NULL, audio_kind TEXT NOT NULL, voice_id TEXT, model_id TEXT,
    text TEXT NOT NULL, text_hash TEXT NOT NULL, settings_json TEXT NOT NULL,
    relative_path TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL,
    sample_rate INTEGER NOT NULL, sample_count INTEGER NOT NULL, provider_request_id TEXT,
    review TEXT NOT NULL CHECK(review IN ('NEEDS_REVIEW','APPROVED','REJECTED')), created_at TEXT NOT NULL,
    UNIQUE(episode_id,line_id,version)
);
-- Shot lengths, pauses and action cues of a whole episode, measured from the takes in use.
CREATE TABLE episode_timing_revisions (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
    version INTEGER NOT NULL, source TEXT NOT NULL, content_json TEXT NOT NULL, content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL, UNIQUE(episode_id,version)
);
-- Dialogue-only mixdown for judging rhythm; scene_key '' is the whole episode.
CREATE TABLE timing_auditions (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
    timing_hash TEXT NOT NULL, scene_key TEXT NOT NULL DEFAULT '', relative_path TEXT NOT NULL, sha256 TEXT NOT NULL,
    bytes INTEGER NOT NULL, sample_rate INTEGER NOT NULL, sample_count INTEGER NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(episode_id,scene_key)
);
