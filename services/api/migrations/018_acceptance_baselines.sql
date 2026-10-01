CREATE TABLE acceptance_baselines (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), version INTEGER NOT NULL,
    payload_json TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(project_id,version)
);
