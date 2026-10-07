import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { errorLabel, stateLabel } from './uiText'
import { t } from './i18n'

type Reference = { id: string; role: string; version: number; provenance: string; review: string; stale: boolean; file_ok: boolean; width: number | null; height: number | null }
type StyleEntity = { id: string; entity_key: string; kind: 'character' | 'environment'; display_name: string; description: string; mapping_status: string | null; profile: { prompt_description: string; voice_mode: string; voice_id: string | null; voice_name: string | null; revision: number }; voice_in_catalog: boolean; references: Reference[]; sample_prompt: string; banned_hits: string[] }
type CaptureJob = { id: string; state: string; items: number; error: string | null; result: { registered?: number; skipped?: { entity_key: string; reason: string }[] } | null }
type StyleState = { draft: { revision: number; style_phrases: string[]; banned_words: string[]; turnaround_map_path: string | null }; entities: StyleEntity[]; lut_name: string; maps: { object_path: string; asset_name: string }[]; roles: Record<string, string[]>; library: { version: number; draft_changed: boolean }; blockers: string[]; warnings: string[]; allowed: boolean; draft_hash: string; bible: { version: number; created_at: string; draft_changed: boolean } | null; capture_jobs: CaptureJob[] }
type VoiceOption = { voice_id: string; name: string; labels: Record<string, string>; preview_url: string | null }

const roleLabel: Record<string, string> = { turnaround_front: t('Turnaround · trước'), turnaround_three_quarter: 'Turnaround · 3/4', turnaround_side: t('Turnaround · ngang'), turnaround_back: t('Turnaround · sau'), frame_01: 'Frame 1', frame_02: 'Frame 2', frame_03: 'Frame 3', frame_04: 'Frame 4', concept: t('Ảnh concept') }
const lines = (text: string) => text.split(/[\n,]/).map(v => v.trim()).filter(Boolean)

export default function StyleBiblePanel({ projectId, refreshKey, unrealConfigured, onStatus }: { projectId: string; refreshKey: string; unrealConfigured: boolean; onStatus: (bible: StyleState['bible']) => void }) {
  const [state, setState] = useState<StyleState | null>(null)
  const [voices, setVoices] = useState<VoiceOption[]>([])
  const [phrases, setPhrases] = useState('')
  const [banned, setBanned] = useState('')
  const [map, setMap] = useState('')
  const [profiles, setProfiles] = useState<Record<string, { prompt: string; voice: string }>>({})
  const [roles, setRoles] = useState<Record<string, string>>({})
  const [stopped, setStopped] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(async () => {
    const [value, voiceList] = await Promise.all([api<StyleState>(`/projects/${projectId}/style`), api<VoiceOption[]>('/settings/voice/voices')])
    setState(value); setVoices(voiceList); onStatus(value.bible)
    return value
  }, [projectId])
  const reset = (value: StyleState) => { setPhrases(value.draft.style_phrases.join('\n')); setBanned(value.draft.banned_words.join('\n')); setMap(value.draft.turnaround_map_path ?? ''); setProfiles({}) }

  useEffect(() => { load().then(reset).catch(e => setError(errorLabel(e))) }, [projectId])
  useEffect(() => { if (state) load().catch(e => setError(errorLabel(e))) }, [refreshKey])
  const working = Boolean(state?.capture_jobs.some(j => ['QUEUED', 'RUNNING'].includes(j.state)))
  useEffect(() => {
    if (!working) return
    const timer = setInterval(() => load().catch(e => setError(errorLabel(e))), 2500)
    return () => clearInterval(timer)
  }, [working, load])

  async function act(action: () => Promise<unknown>, message: string, resetForms = false) {
    setBusy(true); setError(''); setNotice('')
    try { await action(); const value = await load(); if (resetForms) reset(value); setNotice(message) } catch (e) { setError(errorLabel(e)) } finally { setBusy(false) }
  }
  if (!state) return <section className="panel">{error ? <div className="alert error" role="alert">{error}</div> : <p className="help">{t('Đang tải Style bible…')}</p>}</section>
  const job = state.capture_jobs[0]

  return <>
    <section className="panel"><div className="panel-heading"><div><h3>{t('S4 · Tạo Style bible từ thư viện')}</h3><p>{t('Ảnh tham chiếu cho từng nhân vật và bối cảnh, bộ cụm từ style cố định, danh sách từ cấm và giọng ElevenLabs của từng nhân vật.')}</p></div><span className="count">{state.entities.length}{t(' mục')}</span></div>
      {error && <div className="alert error" role="alert">{error}</div>}
      {notice && <div className="alert notice" role="status">{notice}</div>}
      <div className="layout-two">
        <label className="field">{t('Cụm từ style cố định (mỗi dòng một cụm; app tự nối vào mọi prompt)')}<textarea rows={4} value={phrases} onChange={e => setPhrases(e.target.value)} placeholder={'stylized 3D animation\nsoft rim light'} /></label>
        <label className="field">{t('Từ cấm (mỗi dòng một từ)')}<textarea rows={4} value={banned} onChange={e => setBanned(e.target.value)} placeholder="photorealistic" /></label>
      </div>
      <label className="field">{t('Level chụp turnaround nhân vật')}<select value={map} onChange={e => setMap(e.target.value)}><option value="">{t('Chưa chọn')}</option>{state.maps.map(m => <option key={m.object_path} value={m.object_path}>{m.asset_name}</option>)}</select></label>
      <p className="help">{t('Chọn một level có sẵn ánh sáng; nhân vật được đặt tạm tại gốc tọa độ (0, 0, 0) để chụp 4 góc. Bối cảnh được chụp từ các CameraActor đã đặt trong level của nó. App không lưu level hay asset.')}</p>
      <div className="actions">
        <button className="button-primary" disabled={busy} onClick={() => act(() => api(`/projects/${projectId}/style`, { expected_revision: state.draft.revision, style_phrases: lines(phrases), banned_words: lines(banned), turnaround_map_path: map || null }), t('Đã lưu cụm từ style, từ cấm và level chụp.'), true)}>{t('Lưu style')}</button>
        <button disabled={busy || working || !unrealConfigured} onClick={() => act(() => api(`/projects/${projectId}/style/capture`, { entity_ids: null }), t('Đã xếp việc chụp reference; Unreal sẽ mở project để chụp.'))}>{t('Chụp reference từ Unreal')}</button>
      </div>
      {!unrealConfigured && <p className="help">{t('Chưa cấu hình Unreal nên chưa chụp tự động được; vẫn có thể nhập ảnh tham chiếu bằng tay cho từng mục.')}</p>}
      {job && <div className="voice-job-list"><div><span className={`status ${['BLOCKED', 'RECONCILING'].includes(job.state) ? 'warning' : job.state === 'COMPLETED' ? 'success' : 'neutral'}`}>{stateLabel(job.state)}</span>{t(' Lần chụp gần nhất · ')}{job.items}{t(' mục')}{job.result?.registered !== undefined ? t(' · đã đăng ký {0} ảnh', job.result.registered) : ''}{job.error && <small className="row-error"> {job.error}</small>}</div>{job.result?.skipped?.map(s => <div key={s.entity_key + s.reason}><small>{t('Bỏ qua ')}{s.entity_key}: {s.reason}</small></div>)}</div>}
      {job?.state === 'RECONCILING' && <div className="actions"><label className="acceptance-confirm"><input type="checkbox" checked={stopped} onChange={e => setStopped(e.target.checked)} />{t('Tôi đã đóng cửa sổ Unreal của lần chụp bị gián đoạn.')}</label><button disabled={busy || !stopped} onClick={() => act(async () => { await api(`/style/capture/${job.id}/release`, { worker_stopped: stopped }); setStopped(false) }, t('Đã giải phóng hàng đợi; có thể chụp lại.'))}>{t('Xác nhận và giải phóng')}</button></div>}
      <p className="help">{t('Script chụp là bản ứng viên, chưa kiểm chứng trên Unreal 5.8 thật. Ảnh chụp cần FFmpeg (Cài đặt) để chuẩn hóa và luôn ở trạng thái cần duyệt.')}</p>
      {voices.length === 0 && <p className="help">{t('Chưa có danh sách giọng. Vào Cài đặt → Giọng thoại · ElevenLabs để nhập khóa và tải danh sách, hoặc đánh dấu nhân vật không có thoại.')}</p>}

      {state.entities.map(e => {
        const draft = profiles[e.id] ?? { prompt: e.profile.prompt_description, voice: e.profile.voice_mode === 'ELEVENLABS' ? e.profile.voice_id ?? '' : e.profile.voice_mode }
        const setDraft = (change: Partial<typeof draft>) => setProfiles({ ...profiles, [e.id]: { ...draft, ...change } })
        const role = roles[e.id] ?? state.roles[e.kind][0]
        const voice = voices.find(v => v.voice_id === draft.voice)
        return <div key={e.id} className="series-entity"><div className="series-entity-head"><strong>{e.display_name}</strong><span className="muted">{e.kind === 'character' ? t('Nhân vật') : t('Bối cảnh')} · <code>{e.entity_key}</code></span><span className={`status ${e.mapping_status === 'MAPPED' ? 'success' : 'warning'}`}>{e.mapping_status ? stateLabel(e.mapping_status) : t('Chưa chọn asset')}</span></div>
          <div className="planner-fields">
            <label className="field">{t('Mô tả hình ảnh cho prompt (nên viết tiếng Anh)')}<input value={draft.prompt} onChange={ev => setDraft({ prompt: ev.target.value })} placeholder={e.description || t('Ví dụ: middle-aged ship captain in a navy coat')} /></label>
            {e.kind === 'character' && <label className="field">{t('Giọng ElevenLabs')}<select value={draft.voice} onChange={ev => setDraft({ voice: ev.target.value })}><option value="UNSET">{t('Chưa gán')}</option><option value="NO_DIALOGUE">{t('Nhân vật không có thoại')}</option>{voices.map(v => <option key={v.voice_id} value={v.voice_id}>{v.name}{v.labels.gender ? ` · ${v.labels.gender}` : ''}{v.labels.accent ? ` · ${v.labels.accent}` : ''}</option>)}</select></label>}
            <button disabled={busy || !profiles[e.id]} onClick={() => act(() => api(`/series/entities/${e.id}/style-profile`, { expected_revision: e.profile.revision, prompt_description: draft.prompt, voice_mode: ['UNSET', 'NO_DIALOGUE'].includes(draft.voice) ? draft.voice : 'ELEVENLABS', voice_id: ['UNSET', 'NO_DIALOGUE'].includes(draft.voice) ? null : draft.voice }), t('Đã lưu hồ sơ style của {0}.', e.display_name), true)}>{t('Lưu')}</button>
          </div>
          {voice?.preview_url && <audio controls preload="none" src={voice.preview_url} aria-label={t('Nghe thử giọng {0}', voice.name)} />}
          {!e.voice_in_catalog && <p className="row-error">{t('Giọng đã gán không còn trong danh sách ElevenLabs; chọn lại.')}</p>}
          <p className="help">{t('Prompt mẫu: ')}<code>{e.sample_prompt || '—'}</code>{e.banned_hits.length > 0 && <span className="row-error">{t(' · chứa từ cấm: ')}{e.banned_hits.join(', ')}</span>}</p>
          <div className="reference-grid">{e.references.map(r => <figure key={r.id} className="reference-card">
            {r.file_ok ? <img src={`/api/series/references/${r.id}/file`} alt={`${e.display_name} · ${roleLabel[r.role] ?? r.role}`} loading="lazy" /> : <div className="reference-missing">{t('File thiếu hoặc bị đổi')}</div>}
            <figcaption><strong>{roleLabel[r.role] ?? r.role} · v{r.version}</strong><small>{r.provenance === 'UE_SCENE_CAPTURE' ? t('Chụp từ Unreal') : t('Ảnh do bạn nhập')}{r.width ? ` · ${r.width}×${r.height}` : ''}</small><span className={`status ${r.stale || r.review === 'REJECTED' ? 'warning' : r.review === 'APPROVED' ? 'success' : 'neutral'}`}>{r.stale ? t('Asset đã đổi') : stateLabel(r.review)}</span>
              <span className="table-actions"><button disabled={busy || !r.file_ok || r.review === 'APPROVED'} onClick={() => act(() => api(`/series/references/${r.id}/review`, { decision: 'APPROVED' }), t('Đã duyệt reference.'))}>{t('Duyệt')}</button><button disabled={busy || !r.file_ok || r.review === 'REJECTED'} onClick={() => act(() => api(`/series/references/${r.id}/review`, { decision: 'REJECTED' }), t('Đã từ chối reference.'))}>{t('Từ chối')}</button><button className="button-quiet" disabled={busy} onClick={() => act(() => api(`/series/references/${r.id}/remove`, {}), t('Đã gỡ reference khỏi Style bible; file vẫn nằm trong dự án.'))}>{t('Gỡ')}</button></span></figcaption></figure>)}</div>
          <div className="form-row"><label className="field">{t('Nhập ảnh tham chiếu cho vai trò')}<select value={role} onChange={ev => setRoles({ ...roles, [e.id]: ev.target.value })}>{state.roles[e.kind].map(r => <option key={r} value={r}>{roleLabel[r] ?? r}</option>)}</select></label><button disabled={busy} onClick={() => act(() => api(`/series/entities/${e.id}/references/browse`, { role }), t('Đã nhập ảnh vào dự án; cần duyệt trước CS2.'))}>{t('Chọn ảnh PNG/JPEG…')}</button></div>
        </div>
      })}
    </section>

    <section className="panel"><div className="panel-heading"><div><h3>{t('CS2 · Duyệt Style bible → đăng ký')}</h3><p>{t('Duyệt reference từng nhân vật, bối cảnh, LUT và prompt mẫu rồi đăng ký một version Style bible gắn với version thư viện hiện tại.')}</p></div><span className={`status ${state.bible && !state.bible.draft_changed ? 'success' : state.allowed ? 'neutral' : 'warning'}`}>{state.bible ? `Style bible v${state.bible.version}${state.bible.draft_changed ? t(' · có thay đổi chưa duyệt') : ''}` : t('Chưa duyệt')}</span></div>
      <div className="info-line"><span>{t('LUT chuẩn của series (S1)')}</span><strong>{state.lut_name || t('Chưa đặt tên LUT')}</strong></div>
      <div className="info-line"><span>{t('Thư viện đang gắn')}</span><strong>{state.library.version ? `v${state.library.version}${state.library.draft_changed ? t(' · bản nháp đã đổi') : ''}` : t('Chưa duyệt CS1')}</strong></div>
      {state.blockers.length > 0 && <div className="preflight blocked settings-result"><strong>{t('Chưa duyệt được CS2:')}</strong><ul>{state.blockers.map(b => <li key={b}>{b}</li>)}</ul></div>}
      {state.warnings.length > 0 && <div className="preflight ready settings-result"><strong>{t('Lưu ý:')}</strong><ul>{state.warnings.map(w => <li key={w}>{w}</li>)}</ul></div>}
      <p className="help">{t('Bước “test vài shot AI với reference” chưa chạy được vì app chưa có kết nối video AI; version Style bible ghi rõ điều này và sẽ cần duyệt lại khi có.')}</p>
      <div className="actions"><button className="button-primary" disabled={busy || !state.allowed || Boolean(state.bible && !state.bible.draft_changed)} onClick={() => act(() => api(`/projects/${projectId}/style/cs2/approve`, { expected_draft_hash: state.draft_hash }), t('Đã duyệt CS2 và đăng ký version Style bible mới.'))}>{t('Duyệt CS2 → đăng ký Style bible v')}{(state.bible?.version ?? 0) + 1}</button></div>
      {state.bible && <p className="help">Style bible v{state.bible.version}{t(' duyệt lúc ')}{state.bible.created_at.slice(0, 16).replace('T', ' ')}.</p>}
    </section>
  </>
}
