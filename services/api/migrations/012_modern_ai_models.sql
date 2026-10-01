-- Keep explicitly saved paid-provider choices; upgrade only the offline default.
UPDATE ai_settings
SET model='gpt-6.1-sol', max_output_tokens=8192, updated_at=datetime('now')
WHERE id=1 AND provider_kind='LOCAL_PLANNER_FIXTURE' AND model='gpt-4o-mini-2024-07-18';
ALTER TABLE planning_attempts ADD COLUMN provider_model TEXT;
ALTER TABLE planning_drafts ADD COLUMN provider_model TEXT;
