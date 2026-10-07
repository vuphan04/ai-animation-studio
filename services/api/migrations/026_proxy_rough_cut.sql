-- Workflow V4.5: cache and proxy of the UE branch (09/C6), rough cut and Picture Lock (C7).
-- What the operator did about hair, cloth and VFX simulation of a shot, pinned to the motion locked at C5.
CREATE TABLE ue_cache_states (
    episode_id TEXT NOT NULL REFERENCES episodes(id), shot_id TEXT NOT NULL, project_id TEXT NOT NULL REFERENCES projects(id),
    subsystem TEXT NOT NULL CHECK(subsystem IN ('HAIR','CLOTH','VFX')),
    status TEXT NOT NULL CHECK(status IN ('BAKED','NOT_APPLICABLE')), note TEXT NOT NULL DEFAULT '',
    build_hash TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(episode_id,shot_id,subsystem)
);
CREATE TABLE ue_proxy_jobs (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
    operation_id TEXT NOT NULL UNIQUE, input_hash TEXT NOT NULL, request_json TEXT NOT NULL, state TEXT NOT NULL,
    stage TEXT NOT NULL, result_json TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
-- A low-resolution render of one UE or Hybrid-source shot, used only for the rough cut.
CREATE TABLE ue_proxies (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
    shot_id TEXT NOT NULL, version INTEGER NOT NULL, job_id TEXT, provenance TEXT NOT NULL, build_hash TEXT NOT NULL,
    relative_path TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, frames INTEGER NOT NULL,
    width INTEGER NOT NULL, height INTEGER NOT NULL, inspection_json TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(episode_id,shot_id,version)
);
ALTER TABLE ue_shot_states ADD COLUMN c6_hash TEXT;
CREATE TABLE rough_cut_jobs (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
    scene_key TEXT NOT NULL DEFAULT '', operation_id TEXT NOT NULL UNIQUE, input_hash TEXT NOT NULL, state TEXT NOT NULL,
    error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
-- The assembled review copy of a scene ('' is the whole episode): proxies and drafts with the approved dialogue.
CREATE TABLE rough_cuts (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), episode_id TEXT NOT NULL REFERENCES episodes(id),
    scene_key TEXT NOT NULL DEFAULT '', input_hash TEXT NOT NULL, relative_path TEXT NOT NULL, sha256 TEXT NOT NULL,
    bytes INTEGER NOT NULL, frames INTEGER NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(episode_id,scene_key)
);
-- Picture Lock of one scene, pinned to exactly what its rough cut was made from.
CREATE TABLE scene_picture_locks (
    episode_id TEXT NOT NULL REFERENCES episodes(id), scene_key TEXT NOT NULL, project_id TEXT NOT NULL REFERENCES projects(id),
    lock_hash TEXT NOT NULL, rough_cut_sha256 TEXT NOT NULL, checks_json TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY(episode_id,scene_key)
);
