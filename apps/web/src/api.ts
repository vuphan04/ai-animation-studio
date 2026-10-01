export type Project = { id: string; name: string; root: string }
export type Episode = { id: string; name: string; ordinal: number }
export type Asset = { id: string; registry_id: string; kind: string; version: number; metadata: Record<string, unknown> }
export type State = { subsystem: string; mode: 'AUTO' | 'MANUAL'; lock_state: 'LOCKED' | 'UNLOCKED'; validity: 'CURRENT' | 'STALE'; review: 'NEEDS_REVIEW' | 'APPROVED'; state_revision: number; synced_fingerprint: string | null; observed_fingerprint: string | null }
export type Shot = { id: string; project_id: string; episode_id: string; code: string; ordinal: number; current_revision: number; manifest: Record<string, any>; input_hash: string; states: State[]; approval: { exact_version: number; decision: { approved_route: string } } | null }
export type Job = { id: string; shot_id: string; subsystem: string; state: string; operation_id: string; input_hash: string; adapter_kind: string; error: string | null; created_at: string }
export type UnrealJob = { id: string; episode_id: string; state: string; attempt: number; operation_id: string; error: string | null; result?: { output_fingerprint: string; shots: unknown[] } }
export type UnrealPreview = { operation_id: string; input_hash: string; master_path: string; world_path: string; shots: { shot_code: string; timeline: { in: number; out_exclusive: number }; actors: unknown[] }[] }
export type UnrealSettings = { revision: number; editor_path: string; project_path: string; source: 'APP' | 'ENVIRONMENT'; configured: boolean; error: string | null }
export type AISettings = { revision: number; provider_kind: 'LOCAL_PLANNER_FIXTURE' | 'OPENAI'; model: string; max_output_tokens: number; key_configured: boolean; configured: boolean; updated_at: string; available_models: { id: string; label: string; input_rate: number; output_rate: number; legacy: boolean }[] }
export type VoiceJob = { id: string; shot_id: string; performance_id: string; state: string; attempt: number; operation_id: string; error: string | null }
export type VoiceTake = { id: string; shot_id: string; performance_id: string; version: number; provider_kind: string; provider_request_id: string; relative_path: string; sample_rate: number; sample_count: number; review: string; lock_state: string; validity: string; wav_sha256: string }
export type Timing = { id: string; shot_id: string; version: number; shot_revision: number; input_hash: string; review: string; validity: string; placements: { performance_id: string; character_id: string; dialogue_id: string; voice_take_id: string; voice_version: number; start_local_frame: number; end_local_exclusive: number; start_episode_frame: number; end_episode_exclusive: number }[] }
export type TimingPreview = { shot_id: string; shot_revision: number; input_hash: string; allowed: boolean; blockers: string[]; placements: Timing['placements'] }
export type VoiceLockPreview = { take_id: string; take_version: number; shot_revision: number; selected_take_id: string | null; selection_revision: number; allowed: boolean; duration_frames: number; overflow_frames: number; affected_subsystems: string[]; stale_artifacts: Pick<Artifact, 'id' | 'role' | 'version'>[] }
export type Artifact = { id: string; shot_id: string; role: string; version: number; validity: string; provenance: string }
export type Preview = { changed_fields: string[]; affected_subsystems: string[]; stale_artifacts: Artifact[]; media_impact?: Record<string, number>; new_input_hash: string }

let sessionToken = ''

export async function bootstrap(): Promise<{ unreal_configured: boolean; build_id: string | null }> {
  const response = await fetch('/api/session', { credentials: 'same-origin', cache: 'no-store' })
  if (!response.ok) throw new Error(`Không mở được phiên làm việc (HTTP ${response.status}).`)
  const data = await response.json()
  sessionToken = data.session_token
  return { unreal_configured: Boolean(data.unreal_configured), build_id: typeof data.build_id === 'string' ? data.build_id : null }
}

export async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store',
    headers: body === undefined ? {} : { 'Content-Type': 'application/json', 'X-Session-Token': sessionToken },
    body: body === undefined ? undefined : JSON.stringify(body)
  })
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail))
  return data as T
}
