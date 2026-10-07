-- Workflow V4.5 foundation: workflow version, checkpoint approvals, monthly cost ledger, tool paths.
-- Existing projects keep the V1 workflow; old approvals are never reinterpreted as V4.5 checkpoints.
ALTER TABLE projects ADD COLUMN workflow_version TEXT NOT NULL DEFAULT 'V1';
CREATE TABLE workflow_checkpoints (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
    checkpoint TEXT NOT NULL, scope_kind TEXT NOT NULL, scope_id TEXT NOT NULL,
    exact_version INTEGER NOT NULL, dependency_hash TEXT NOT NULL, decision_json TEXT NOT NULL,
    actor TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(project_id,checkpoint,scope_kind,scope_id,exact_version)
);
CREATE TABLE budget_policies (
    project_id TEXT PRIMARY KEY REFERENCES projects(id), revision INTEGER NOT NULL,
    monthly_cap_micro_usd INTEGER NOT NULL DEFAULT 0, monthly_cap_micro_cny INTEGER NOT NULL DEFAULT 0,
    warn_percent INTEGER NOT NULL DEFAULT 80, paid_paused INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);
CREATE TABLE cost_ledger (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), month TEXT NOT NULL,
    gate INTEGER NOT NULL, category TEXT NOT NULL, provider_kind TEXT NOT NULL, currency TEXT NOT NULL,
    operation_id TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK(state IN ('RESERVED','SETTLED','RELEASED')),
    reserved_micro INTEGER NOT NULL, actual_micro INTEGER, enforced INTEGER NOT NULL DEFAULT 1, note TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX idx_cost_ledger_month ON cost_ledger(project_id,month,currency);
CREATE TABLE tool_settings (
    id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, ffmpeg_path TEXT NOT NULL, updated_at TEXT NOT NULL
);
