CREATE TABLE unreal_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    editor_path TEXT NOT NULL,
    project_path TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
