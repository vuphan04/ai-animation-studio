import { lang, t } from './i18n'
export type Project = { id: string; name: string; root: string }
export type Episode = { id: string; name: string; ordinal: number }
export type UnrealSettings = { revision: number; editor_path: string; project_path: string; source: 'APP' | 'ENVIRONMENT'; configured: boolean; error: string | null }
export type AISettings = { revision: number; provider_kind: 'LOCAL_PLANNER_FIXTURE' | 'OPENAI' | 'CLAUDE_API' | 'CLAUDE_CODE'; model: string; max_output_tokens: number; key_configured: boolean; configured: boolean; updated_at: string; available_models: { id: string; label: string; input_rate: number; output_rate: number; legacy: boolean }[]
  claude_model: string; claude_effort: string; claude_max_output_tokens: number; claude_fallback: boolean; claude_key_configured: boolean
  claude_models: { id: string; label: string; input_rate: number; output_rate: number; fallback: boolean; long_prompt: { above_tokens: number; input_rate: number; output_rate: number } | null }[]
  claude_efforts: { id: string; label: string }[] }
export type FlowState = 'DONE' | 'STALE' | 'PARTIAL' | 'TODO'
export type Checkpoint = { key: string; label: string; page: string; step: string; state: FlowState; detail: string }
export type Progress = { episode_id: string | null; zones: { key: string; label: string; page: string; checkpoints: Checkpoint[] }[]; next: Checkpoint | null; done: number; total: number }
export type GpuQueue = { lease: { owner_kind: string; state: string } | null; waiting: { unreal: number; renders: number; scans: number; foley: number } }

let sessionToken = ''
// The server writes its messages in Vietnamese and translates them for an English interface.
const language: Record<string, string> = lang === 'en' ? { 'X-Studio-Lang': 'en' } : {}

export async function bootstrap(): Promise<{ unreal_configured: boolean; build_id: string | null }> {
  const response = await fetch('/api/session', { credentials: 'same-origin', cache: 'no-store', headers: language })
  if (!response.ok) throw new Error(t('Không mở được phiên làm việc (HTTP {0}).', response.status))
  const data = await response.json()
  sessionToken = data.session_token
  return { unreal_configured: Boolean(data.unreal_configured), build_id: typeof data.build_id === 'string' ? data.build_id : null }
}

export async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store',
    headers: body === undefined ? language : { ...language, 'Content-Type': 'application/json', 'X-Session-Token': sessionToken },
    body: body === undefined ? undefined : JSON.stringify(body)
  })
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail))
  return data as T
}
