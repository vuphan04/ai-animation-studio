CREATE TABLE ai_settings (
    id INTEGER PRIMARY KEY CHECK(id=1),
    revision INTEGER NOT NULL,
    provider_kind TEXT NOT NULL,
    model TEXT NOT NULL,
    max_output_tokens INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
INSERT INTO ai_settings(id,revision,provider_kind,model,max_output_tokens,updated_at)
VALUES(1,1,'LOCAL_PLANNER_FIXTURE','gpt-4o-mini-2024-07-18',4096,datetime('now'));
