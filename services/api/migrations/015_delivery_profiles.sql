CREATE TABLE delivery_profiles (
    episode_id TEXT PRIMARY KEY REFERENCES episodes(id), revision INTEGER NOT NULL,
    width INTEGER NOT NULL, height INTEGER NOT NULL, color_profile TEXT NOT NULL,
    audio_sample_rate INTEGER NOT NULL, timecode_start TEXT NOT NULL, updated_at TEXT NOT NULL
);
