-- Claude through the Anthropic API with the operator's own key. The key lives in Windows Credential Manager, never here.
ALTER TABLE ai_settings ADD COLUMN claude_model TEXT NOT NULL DEFAULT 'claude-opus-5-5';
ALTER TABLE ai_settings ADD COLUMN claude_effort TEXT NOT NULL DEFAULT 'medium';
ALTER TABLE ai_settings ADD COLUMN claude_max_output_tokens INTEGER NOT NULL DEFAULT 16000;
ALTER TABLE ai_settings ADD COLUMN claude_fallback INTEGER NOT NULL DEFAULT 1;
