-- Workflow V4.5 zone A: episode breakdown kept apart from execution manifests (03, C1, 04, C2).
-- Episode code (EP01…) is the stable prefix of shot IDs; the display name stays free-form.
ALTER TABLE episodes ADD COLUMN code TEXT;
UPDATE episodes SET code = 'EP' || printf('%02d', ordinal);
CREATE TABLE breakdown_jobs (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
    screenplay_id TEXT NOT NULL REFERENCES screenplay_versions(id), provider_kind TEXT NOT NULL, provider_model TEXT,
    operation_id TEXT NOT NULL UNIQUE, input_hash TEXT NOT NULL, state TEXT NOT NULL,
    estimated_micro INTEGER NOT NULL, actual_micro INTEGER, provider_request_id TEXT, revision_version INTEGER, error TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE breakdown_revisions (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
    version INTEGER NOT NULL, screenplay_id TEXT REFERENCES screenplay_versions(id), source TEXT NOT NULL,
    library_version INTEGER NOT NULL, style_version INTEGER NOT NULL,
    content_json TEXT NOT NULL, content_hash TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(episode_id,version)
);
-- A scene approval is pinned to the scene's own content hash, so editing one scene never clears the others.
CREATE TABLE breakdown_scene_approvals (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
    scene_key TEXT NOT NULL, scene_hash TEXT NOT NULL, revision_version INTEGER NOT NULL, actor TEXT NOT NULL,
    created_at TEXT NOT NULL, UNIQUE(episode_id,scene_key,scene_hash)
);
