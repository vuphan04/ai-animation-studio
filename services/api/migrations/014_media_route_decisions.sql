CREATE TABLE media_route_decisions (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), shot_id TEXT NOT NULL REFERENCES shots(id),
    shot_revision INTEGER NOT NULL, version INTEGER NOT NULL, route TEXT NOT NULL,
    fallback_take_id TEXT REFERENCES video_takes(id), actor TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(shot_id,shot_revision,version)
);
CREATE TABLE media_asset_references (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
    asset_version_id TEXT NOT NULL REFERENCES asset_versions(id), version INTEGER NOT NULL,
    relative_path TEXT NOT NULL, sha256 TEXT NOT NULL, review TEXT NOT NULL,
    created_at TEXT NOT NULL, UNIQUE(asset_version_id,version)
);
