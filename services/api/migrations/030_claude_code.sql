-- Claude through the Claude Code program installed on this machine: which executable and model to use.
-- No account data is stored; sign-in lives inside Claude Code.
CREATE TABLE claude_code_settings (
    id INTEGER PRIMARY KEY CHECK(id=1),
    revision INTEGER NOT NULL,
    executable_path TEXT NOT NULL,
    model TEXT NOT NULL,
    verified_at TEXT,
    verified_model TEXT,
    updated_at TEXT NOT NULL
);
INSERT INTO claude_code_settings(id,revision,executable_path,model,verified_at,verified_model,updated_at)
VALUES(1,1,'','opus',NULL,NULL,datetime('now'));
