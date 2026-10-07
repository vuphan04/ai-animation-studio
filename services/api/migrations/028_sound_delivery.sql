-- Workflow V4.5 zone C: Foley and sound (12/C9), assembly, timeline package and QC (13), Resolve hand-off (C10).
-- The V1 tables sound_stems, foley_jobs and delivery_packages stay as they are; nothing here reads them.
CREATE TABLE foley_tool_settings (
    id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, python_path TEXT NOT NULL, script_path TEXT NOT NULL,
    model_path TEXT NOT NULL, config_path TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE episode_foley_jobs (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
    shot_id TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE, input_hash TEXT NOT NULL, request_json TEXT NOT NULL,
    state TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX idx_episode_foley_jobs_state ON episode_foley_jobs(state,created_at);
-- Everything on the sound tracks except dialogue: Foley per shot, and the SFX, ambience and music the operator brings in.
-- A clip is placed relative to a shot, so it moves with that shot when timing changes.
CREATE TABLE episode_sound_clips (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
    kind TEXT NOT NULL CHECK(kind IN ('FOLEY','SFX','AMBIENCE','MUSIC')), shot_id TEXT NOT NULL,
    offset_frames INTEGER NOT NULL DEFAULT 0, length_frames INTEGER, gain_mdb INTEGER NOT NULL DEFAULT 0,
    fade_in_frames INTEGER NOT NULL DEFAULT 0, fade_out_frames INTEGER NOT NULL DEFAULT 0, label TEXT NOT NULL,
    provenance TEXT NOT NULL, prompt TEXT, job_id TEXT, basis_hash TEXT,
    relative_path TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL,
    sample_rate INTEGER NOT NULL, channels INTEGER NOT NULL, duration_ms INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1, revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX idx_episode_sound_clips ON episode_sound_clips(episode_id,kind,shot_id);
CREATE TABLE episode_sound_jobs (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
    operation_id TEXT NOT NULL UNIQUE, input_hash TEXT NOT NULL, state TEXT NOT NULL, error TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
-- One build: the five stems, their mix and a review copy with the locked picture.
CREATE TABLE episode_sound_builds (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
    version INTEGER NOT NULL, input_hash TEXT NOT NULL, relative_dir TEXT NOT NULL, stems_json TEXT NOT NULL,
    loudness_json TEXT NOT NULL, sample_rate INTEGER NOT NULL, sample_count INTEGER NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(episode_id,version)
);
CREATE TABLE episode_delivery_jobs (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
    operation_id TEXT NOT NULL UNIQUE, input_hash TEXT NOT NULL, request_json TEXT NOT NULL, state TEXT NOT NULL, stage TEXT NOT NULL,
    error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
-- Step 13: one package folder per version, with the timeline, media, subtitles, preview and the automatic QC report.
CREATE TABLE episode_deliveries (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
    version INTEGER NOT NULL, input_hash TEXT NOT NULL, relative_dir TEXT NOT NULL, inventory_json TEXT NOT NULL,
    qc_json TEXT NOT NULL, frames INTEGER NOT NULL, created_at TEXT NOT NULL, UNIQUE(episode_id,version)
);
-- C10: the film the operator exported from Resolve. The file stays where the operator saved it.
CREATE TABLE episode_exports (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
    delivery_id TEXT NOT NULL REFERENCES episode_deliveries(id), path TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL,
    info_json TEXT NOT NULL, created_at TEXT NOT NULL
);
