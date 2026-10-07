-- Workflow V4.5 zone 0: Style bible (S4) and its approval (CS2), plus the ElevenLabs voice catalog used to assign voices.
CREATE TABLE voice_provider_settings (
    id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, provider TEXT NOT NULL, model_id TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE voice_catalog (
    provider TEXT NOT NULL, voice_id TEXT NOT NULL, name TEXT NOT NULL, category TEXT, labels_json TEXT NOT NULL,
    preview_url TEXT, fetched_at TEXT NOT NULL, PRIMARY KEY(provider,voice_id)
);
CREATE TABLE voice_model_catalog (
    provider TEXT NOT NULL, model_id TEXT NOT NULL, name TEXT NOT NULL, languages_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL, PRIMARY KEY(provider,model_id)
);
CREATE TABLE style_bible_drafts (
    project_id TEXT PRIMARY KEY REFERENCES projects(id), revision INTEGER NOT NULL,
    style_phrases_json TEXT NOT NULL, banned_words_json TEXT NOT NULL, turnaround_map_path TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE style_entity_profiles (
    entity_id TEXT PRIMARY KEY REFERENCES series_entities(id), project_id TEXT NOT NULL REFERENCES projects(id),
    prompt_description TEXT NOT NULL,
    voice_mode TEXT NOT NULL CHECK(voice_mode IN ('UNSET','ELEVENLABS','NO_DIALOGUE')),
    voice_id TEXT, voice_name TEXT, revision INTEGER NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE style_references (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), entity_id TEXT NOT NULL REFERENCES series_entities(id),
    role TEXT NOT NULL, version INTEGER NOT NULL, relative_path TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL,
    width INTEGER, height INTEGER, provenance TEXT NOT NULL, source_asset_path TEXT,
    review TEXT NOT NULL CHECK(review IN ('NEEDS_REVIEW','APPROVED','REJECTED')), active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, UNIQUE(entity_id,role,version)
);
CREATE TABLE style_capture_jobs (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), operation_id TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL, request_json TEXT NOT NULL, result_json TEXT, error TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE style_bible_versions (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), version INTEGER NOT NULL,
    library_version INTEGER NOT NULL, snapshot_json TEXT NOT NULL, snapshot_hash TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(project_id,version)
);
