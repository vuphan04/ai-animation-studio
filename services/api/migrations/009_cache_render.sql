CREATE TABLE simulation_cache_versions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    shot_id TEXT NOT NULL REFERENCES shots(id),
    subsystem TEXT NOT NULL CHECK(subsystem IN ('hair','cloth','vfx')),
    version INTEGER NOT NULL,
    shot_revision INTEGER NOT NULL,
    source_json TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    range_in INTEGER NOT NULL,
    range_out_exclusive INTEGER NOT NULL,
    relative_path TEXT NOT NULL,
    output_sha256 TEXT NOT NULL,
    playback_status TEXT NOT NULL CHECK(playback_status IN ('UNVERIFIED','VERIFIED')),
    playback_receipt_json TEXT,
    review TEXT NOT NULL DEFAULT 'NEEDS_REVIEW',
    validity TEXT NOT NULL DEFAULT 'CURRENT',
    provenance TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(shot_id,subsystem,version)
);
CREATE INDEX idx_cache_shot ON simulation_cache_versions(shot_id,subsystem,version);
CREATE TABLE simulation_applicability (
    shot_id TEXT NOT NULL REFERENCES shots(id),
    subsystem TEXT NOT NULL CHECK(subsystem IN ('hair','cloth','vfx')),
    status TEXT NOT NULL CHECK(status IN ('PRESENT','NOT_APPLICABLE')),
    reason TEXT,
    observation_receipt_json TEXT,
    source_json TEXT,
    input_fingerprint TEXT,
    shot_revision INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(shot_id,subsystem)
);
CREATE TABLE render_inventories (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    shot_id TEXT NOT NULL REFERENCES shots(id),
    kind TEXT NOT NULL CHECK(kind IN ('PROXY','FINAL')),
    version INTEGER NOT NULL,
    shot_revision INTEGER NOT NULL,
    input_hash TEXT NOT NULL,
    preset_version_id TEXT,
    fps_num INTEGER NOT NULL,
    fps_den INTEGER NOT NULL,
    color_profile TEXT NOT NULL,
    relative_folder TEXT NOT NULL,
    expected_in INTEGER NOT NULL,
    expected_out_exclusive INTEGER NOT NULL,
    frame_inventory_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('PARTIAL','FILES_COMPLETE_UNVERIFIED')),
    provenance TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(shot_id,kind,version)
);
CREATE TABLE gpu_heavy_leases (
    resource TEXT PRIMARY KEY CHECK(resource='GPU_HEAVY'),
    operation_id TEXT NOT NULL UNIQUE,
    owner_kind TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('ACTIVE','RECONCILING'))
);
