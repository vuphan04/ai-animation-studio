-- Workflow V4.5 zone 0: series library (S1 standards, S2 entities, S3 UE scan, CS1 mapping).
CREATE TABLE series_standards (
    project_id TEXT PRIMARY KEY REFERENCES projects(id), revision INTEGER NOT NULL,
    fps_num INTEGER NOT NULL, fps_den INTEGER NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
    color_space TEXT NOT NULL, lut_name TEXT NOT NULL, default_video_model TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE series_entities (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), entity_key TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('character','environment')), display_name TEXT NOT NULL,
    aliases_json TEXT NOT NULL, description TEXT NOT NULL, scenes_json TEXT NOT NULL, source TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1, merged_into TEXT, revision INTEGER NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(project_id,entity_key)
);
CREATE TABLE series_extraction_jobs (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
    screenplay_id TEXT NOT NULL REFERENCES screenplay_versions(id), provider_kind TEXT NOT NULL, provider_model TEXT,
    operation_id TEXT NOT NULL UNIQUE, input_hash TEXT NOT NULL, state TEXT NOT NULL,
    estimated_micro INTEGER NOT NULL, actual_micro INTEGER, provider_request_id TEXT, result_json TEXT, error TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE ue_asset_scans (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), operation_id TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL, ue_project_path TEXT NOT NULL, engine_version TEXT, asset_count INTEGER,
    inventory_sha256 TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE ue_scan_assets (
    scan_id TEXT NOT NULL REFERENCES ue_asset_scans(id), object_path TEXT NOT NULL, asset_name TEXT NOT NULL,
    package_path TEXT NOT NULL, asset_class TEXT NOT NULL, category TEXT NOT NULL, skeleton_path TEXT, parent_class TEXT,
    PRIMARY KEY(scan_id,object_path)
);
CREATE INDEX idx_ue_scan_assets_category ON ue_scan_assets(scan_id,category,package_path);
CREATE TABLE series_mappings (
    entity_id TEXT PRIMARY KEY REFERENCES series_entities(id), project_id TEXT NOT NULL REFERENCES projects(id),
    status TEXT NOT NULL CHECK(status IN ('MAPPED','MISSING')), scan_id TEXT REFERENCES ue_asset_scans(id),
    asset_path TEXT, body_mesh_path TEXT, face_mesh_path TEXT, note TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE series_library_drafts (
    project_id TEXT PRIMARY KEY REFERENCES projects(id), revision INTEGER NOT NULL,
    animation_folders_json TEXT NOT NULL, vfx_folders_json TEXT NOT NULL, retargeters_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE series_library_versions (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), version INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL, snapshot_hash TEXT NOT NULL, scan_id TEXT NOT NULL REFERENCES ue_asset_scans(id),
    created_at TEXT NOT NULL, UNIQUE(project_id,version)
);
