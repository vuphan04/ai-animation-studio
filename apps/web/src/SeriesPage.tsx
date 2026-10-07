import { useCallback, useEffect, useState } from 'react'
import { api, type Episode, type FlowState } from './api'
import StepTabs, { type StepDef } from './StepTabs'
import { errorLabel, stateLabel } from './uiText'
import StyleBiblePanel from './StyleBiblePanel'
import { t } from './i18n'

type Standards = { revision: number; fps_num: number; fps_den: number; width: number; height: number; color_space: string; lut_name: string; default_video_model: string; saved: boolean; options: { color_spaces: string[]; video_models: string[] } }
type Total = { currency: string; cap_micro: number; settled_micro: number; reserved_micro: number; remaining_micro: number; warning: boolean; exceeded: boolean }
type LedgerEntry = { id: string; category: string; provider_kind: string; currency: string; gate: number; state: string; reserved_micro: number; actual_micro: number | null; created_at: string }
type Budget = { policy: { revision: number; monthly_cap_micro_usd: number; monthly_cap_micro_cny: number; warn_percent: number; paid_paused: boolean }; month: string; totals: Total[]; entries: LedgerEntry[]; scope_note: string }
type Mapping = { status: 'MAPPED' | 'MISSING'; asset_path: string | null; body_mesh_path: string | null; face_mesh_path: string | null; note: string; revision: number }
type Suggestion = { object_path: string; asset_name: string; category: string; score: number }
type Entity = { id: string; entity_key: string; kind: 'character' | 'environment'; display_name: string; aliases: string[]; description: string; scenes: string[]; source: string; revision: number; mapping?: Mapping | null; suggestions?: Suggestion[] }
type Pair = { source_skeleton: string; animation_count: number; target_entity_key: string; target_skeleton: string | null; mode: string; retargeter_path: string | null }
type MappingState = { entities: Entity[]; draft: { revision: number; animation_folders: string[]; vfx_folders: string[]; retargeters: { source_skeleton: string; target_entity_key: string; mode: string; retargeter_path: string | null }[] }; retarget_pairs: Pair[]; retargeter_assets: { object_path: string; asset_name: string }[]; blockers: string[]; allowed: boolean; draft_hash: string; library: { version: number; created_at: string; draft_changed: boolean } | null; pending_unreal_work: number }
type Scan = { id: string; state: string; engine_version: string | null; asset_count: number | null; error: string | null; created_at: string; categories: Record<string, number> }
type Scans = { scans: Scan[]; latest_completed_id: string | null; current_ue_project_path: string; latest_matches_current_project: boolean; target_engine: string }
type ExtractionJob = { id: string; state: string; provider_kind: string; estimated_micro: number; actual_micro: number | null; error: string | null; result: { applied?: { created: number; updated: number } } | null }
type ExtractionPreview = { provider_kind: string; provider_model: string | null; paid: boolean; estimated_micro: number; budget: { allowed: boolean; reason: string | null }; note: string | null }
type Screenplay = { id: string; version: number; language: string; text: string }
type ScanAsset = { object_path: string; asset_name: string; package_path: string; category: string; skeleton_path: string | null }
type Folder = { path: string; count: number; depth: number }

const money = (micro: number) => (micro / 1e6).toFixed(2)
const kindLabel = { character: t('Nhân vật'), environment: t('Bối cảnh') }
const modelLabel: Record<string, string> = { SEEDANCE_2_5: 'Seedance 2.5', MINIMAX_H3: 'MiniMax H3 (Hailuo 3)' }
const ledgerLabel: Record<string, string> = { PLANNING: t('Phân tích kịch bản'), SERIES_EXTRACT: t('Liệt kê nhân vật, bối cảnh'), VOICE: t('Thoại ElevenLabs'), VIDEO_DRAFT: t('Video draft'), VIDEO_FINAL: t('Video final'), OTHER: t('Khác') }
const categoryLabel: Record<string, string> = { character_bp: t('BP nhân vật'), metahuman: 'MetaHuman', blueprint: t('Blueprint khác'), skeletal_mesh: 'Skeletal Mesh', skeleton: 'Skeleton', map: 'Map/Level', animation: 'Animation', niagara: 'Niagara VFX', ik_retargeter: 'IK Retargeter', ik_rig: 'IK Rig', level_sequence: 'Level Sequence' }
const short = (path: string | null) => path ? path.split('.').pop() ?? path : '—'

export function AssetPicker({ projectId, categories, label, value, disabled, clearable = true, onPick }: { projectId: string; categories: string; label: string; value: string | null; disabled: boolean; clearable?: boolean; onPick: (path: string | null) => void }) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [result, setResult] = useState<{ total: number; assets: ScanAsset[] } | null>(null)
  const [error, setError] = useState('')
  async function search(text: string) {
    setError('')
    try { setResult(await api(`/projects/${projectId}/series/scan/assets?category=${encodeURIComponent(categories)}&q=${encodeURIComponent(text)}&limit=40`)) } catch (e) { setError(errorLabel(e)) }
  }
  return <div className="asset-picker"><span className="asset-picker-label">{label}</span><strong title={value ?? ''}>{short(value)}</strong>
    <button type="button" disabled={disabled} onClick={() => { setOpen(!open); if (!open) search(q) }}>{open ? t('Đóng') : t('Chọn từ danh sách…')}</button>
    {value && clearable && <button type="button" className="button-quiet" disabled={disabled} onClick={() => onPick(null)}>{t('Bỏ chọn')}</button>}
    {open && <div className="asset-picker-list"><input value={q} placeholder={t('Tìm theo tên asset')} onChange={e => { setQ(e.target.value); search(e.target.value) }} spellCheck={false} />
      {error && <p className="row-error">{error}</p>}
      {result && (result.assets.length ? <ul>{result.assets.map(a => <li key={a.object_path}><button type="button" onClick={() => { onPick(a.object_path); setOpen(false) }}><strong>{a.asset_name}</strong><small>{categoryLabel[a.category] ?? a.category} · {a.package_path}{a.skeleton_path ? ` · skeleton ${short(a.skeleton_path)}` : ''}</small></button></li>)}</ul> : <p className="help">{t('Không có asset phù hợp trong lần quét mới nhất.')}</p>)}
      {result && result.total > result.assets.length && <p className="help">{t('Hiện ')}{result.assets.length}/{result.total}{t('; gõ thêm để lọc.')}</p>}
    </div>}
  </div>
}

export const seriesSteps: StepDef[] = [
  { id: 's1', code: 'S1', label: t('Chuẩn kỹ thuật & ngân sách') },
  { id: 's2', code: 'S2', label: t('Nhân vật & bối cảnh') },
  { id: 'mapping', code: 'S3 · CS1', label: t('Quét asset & mapping') },
  { id: 'style', code: 'S4 · CS2', label: 'Style bible' },
]

export default function SeriesPage({ projectId, episodes, episodeId, unrealConfigured, step, onStep, marks }: { projectId: string; episodes: Episode[]; episodeId: string; unrealConfigured: boolean; step: string; onStep: (step: string) => void; marks: Record<string, FlowState> }) {
  const [standards, setStandards] = useState<Standards | null>(null)
  const [form, setForm] = useState({ fps_num: 24, fps_den: 1, width: 1920, height: 1080, color_space: 'Rec.709', lut_name: '', default_video_model: 'SEEDANCE_2_5' })
  const [budget, setBudget] = useState<Budget | null>(null)
  const [caps, setCaps] = useState({ usd: '0', cny: '0', warn: 80, paused: false })
  const [resolveOk, setResolveOk] = useState(false)
  const [mapping, setMapping] = useState<MappingState | null>(null)
  const [scans, setScans] = useState<Scans | null>(null)
  const [jobs, setJobs] = useState<ExtractionJob[]>([])
  const [screenplays, setScreenplays] = useState<Screenplay[]>([])
  const [text, setText] = useState('')
  const [minutes, setMinutes] = useState(10)
  const [preview, setPreview] = useState<ExtractionPreview | null>(null)
  const [edits, setEdits] = useState<Record<string, { name: string; aliases: string; description: string }>>({})
  const [mergeTarget, setMergeTarget] = useState<Record<string, string>>({})
  const [newEntity, setNewEntity] = useState({ kind: 'character', name: '' })
  const [folders, setFolders] = useState<{ animation: Folder[]; niagara: Folder[] }>({ animation: [], niagara: [] })
  const [picked, setPicked] = useState<{ animation: string[]; vfx: string[]; decisions: Record<string, string> } | null>(null)
  const [scanStopped, setScanStopped] = useState(false)
  const [bible, setBible] = useState<{ version: number; draft_changed: boolean } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(async () => {
    if (!projectId) return
    const [std, bud, map, sc, jb, anim, vfx] = await Promise.all([
      api<Standards>(`/projects/${projectId}/series/standards`),
      api<Budget>(`/projects/${projectId}/budget`), api<MappingState>(`/projects/${projectId}/series/mapping`),
      api<Scans>(`/projects/${projectId}/series/scans`), api<ExtractionJob[]>(`/projects/${projectId}/series/extraction/jobs`),
      api<Folder[]>(`/projects/${projectId}/series/scan/folders?category=animation`), api<Folder[]>(`/projects/${projectId}/series/scan/folders?category=niagara`)])
    setStandards(std); setBudget(bud); setMapping(map); setScans(sc); setJobs(jb); setFolders({ animation: anim, niagara: vfx })
    return { std, bud, map }
  }, [projectId])

  // Form drafts follow the saved values only on first load and after a save, so a background refresh never erases typing.
  const resetForms = useCallback((std: Standards, bud: Budget, map: MappingState) => {
    setForm({ fps_num: std.fps_num, fps_den: std.fps_den, width: std.width, height: std.height, color_space: std.color_space, lut_name: std.lut_name, default_video_model: std.default_video_model })
    setCaps({ usd: money(bud.policy.monthly_cap_micro_usd), cny: money(bud.policy.monthly_cap_micro_cny), warn: bud.policy.warn_percent, paused: bud.policy.paid_paused })
    setPicked({ animation: map.draft.animation_folders, vfx: map.draft.vfx_folders, decisions: Object.fromEntries(map.draft.retargeters.map(r => [`${r.source_skeleton}|${r.target_entity_key}`, r.mode === 'EXISTING' ? r.retargeter_path ?? '' : 'NEEDS_CREATE'])) })
    setEdits({})
  }, [])

  useEffect(() => {
    setError(''); setNotice(''); setPreview(null); setPicked(null)
    load().then(v => v && resetForms(v.std, v.bud, v.map)).catch(e => setError(errorLabel(e)))
  }, [projectId])

  const working = jobs.some(j => ['QUEUED', 'RUNNING'].includes(j.state)) || Boolean(scans?.scans.some(s => ['QUEUED', 'RUNNING'].includes(s.state)))
  useEffect(() => {
    if (!working) return
    const timer = setInterval(() => load().catch(e => setError(errorLabel(e))), 2500)
    return () => clearInterval(timer)
  }, [working, load])

  useEffect(() => {
    if (!episodeId) { setScreenplays([]); return }
    api<Screenplay[]>(`/episodes/${episodeId}/screenplays`).then(list => { setScreenplays(list); if (list[0]) setText(list[0].text) }).catch(e => setError(errorLabel(e)))
  }, [episodeId])

  async function act(action: () => Promise<unknown>, message: string, reset: 'forms' | 'edits' | false = false) {
    setBusy(true); setError(''); setNotice('')
    try {
      await action()
      const v = await load()
      if (reset === 'forms' && v) resetForms(v.std, v.bud, v.map)
      if (reset === 'edits') setEdits({})
      setNotice(message)
    } catch (e) { setError(errorLabel(e)) } finally { setBusy(false) }
  }

  if (!projectId) return <div className="empty"><span className="empty-icon" aria-hidden="true">◇</span><strong>{t('Chưa chọn dự án')}</strong><p>{t('Chọn dự án ở thanh phía trên để khởi tạo thư viện series.')}</p></div>
  if (!standards || !budget || !mapping || !scans || !picked) return <>{error ? <div className="alert error" role="alert">{error}</div> : <p className="help">{t('Đang tải thư viện series…')}</p>}</>

  const latestScript = screenplays[0]
  const latestScan = scans.scans.find(s => s.id === scans.latest_completed_id)
  const activeScan = scans.scans[0]
  const characters = mapping.entities.filter(e => e.kind === 'character')
  const saveMapping = (entity: Entity, change: Partial<Mapping>) => {
    const current = entity.mapping
    const next = { status: 'MAPPED' as const, asset_path: current?.asset_path ?? null, body_mesh_path: current?.body_mesh_path ?? null, face_mesh_path: current?.face_mesh_path ?? null, note: current?.note ?? '', ...change }
    return act(() => api(`/series/entities/${entity.id}/mapping`, { expected_revision: current?.revision ?? 0, ...next }), t('Đã lưu mapping cho {0}.', entity.display_name))
  }
  const toggle = (list: string[], path: string) => list.includes(path) ? list.filter(p => p !== path) : [...list, path]
  const openPairs = new Set(mapping.retarget_pairs.filter(p => p.mode !== 'DIRECT').map(p => `${p.source_skeleton}|${p.target_entity_key}`))
  const decisionsPayload = () => Object.entries(picked.decisions).filter(([key, value]) => value && openPairs.has(key)).map(([key, value]) => {
    const [source_skeleton, target_entity_key] = key.split('|')
    return { source_skeleton, target_entity_key, mode: value === 'NEEDS_CREATE' ? 'NEEDS_CREATE' : 'EXISTING', retargeter_path: value === 'NEEDS_CREATE' ? null : value }
  })

  return <div className="stack v45 series-page">
    {error && <div className="alert error" role="alert">{error}</div>}
    {notice && <div className="alert notice" role="status">{notice}</div>}
    <StepTabs steps={seriesSteps} value={step} onChange={onStep} label={t('Các bước của Khu 0')} marks={{ s1: standards.saved ? 'DONE' : 'TODO', s2: mapping.entities.length ? 'DONE' : 'TODO',
      mapping: mapping.library ? (mapping.library.draft_changed ? 'STALE' : 'DONE') : marks.mapping ?? 'TODO', style: bible ? (bible.draft_changed ? 'STALE' : 'DONE') : marks.style ?? 'TODO' }} />
    <p className="help step-intro">{t('Chạy một lần với tập đầu tiên. Mọi tập sau bắt đầu từ thư viện này; tập có nhân vật hoặc bối cảnh mới dùng lại đúng các bước dưới đây rồi tăng version thư viện.')}{latestScan ? t(' Lần quét asset gần nhất: {0} asset.', latestScan.asset_count ?? 0) : ''}</p>

    <div className="stack" hidden={step !== 's1'}>
    <section className="panel"><div className="panel-heading"><div><h3>{t('S1 · Chuẩn kỹ thuật, ngân sách & model')}</h3><p>{t('Chuẩn chung cho mọi shot của series. Giá trị được lưu trong app; bước khóa vào project Unreal thuộc phase dựng UE.')}</p></div><span className={`status ${standards.saved ? 'success' : 'neutral'}`}>{standards.saved ? t('Đã lưu · v{0}', standards.revision) : t('Chưa lưu')}</span></div>
      <div className="planner-fields">
        <label className="field">{t('FPS (tử số)')}<input type="number" min={1} value={form.fps_num} onChange={e => setForm({ ...form, fps_num: Number(e.target.value) })} /></label>
        <label className="field">{t('FPS (mẫu số)')}<input type="number" min={1} value={form.fps_den} onChange={e => setForm({ ...form, fps_den: Number(e.target.value) })} /></label>
        <label className="field">{t('Rộng (px)')}<input type="number" min={2} step={2} value={form.width} onChange={e => setForm({ ...form, width: Number(e.target.value) })} /></label>
        <label className="field">{t('Cao (px)')}<input type="number" min={2} step={2} value={form.height} onChange={e => setForm({ ...form, height: Number(e.target.value) })} /></label>
        <label className="field">{t('Không gian màu')}<select value={form.color_space} onChange={e => setForm({ ...form, color_space: e.target.value })}>{standards.options.color_spaces.map(c => <option key={c}>{c}</option>)}</select></label>
        <label className="field">{t('Tên LUT chuẩn')}<input value={form.lut_name} onChange={e => setForm({ ...form, lut_name: e.target.value })} placeholder={t('Ví dụ: Show_LUT_v1')} /></label>
        <label className="field">{t('Model video mặc định')}<select value={form.default_video_model} onChange={e => setForm({ ...form, default_video_model: e.target.value })}>{standards.options.video_models.map(m => <option key={m} value={m}>{modelLabel[m] ?? m}</option>)}</select></label>
      </div>
      <div className="actions"><button className="button-primary" disabled={busy} onClick={() => act(() => api(`/projects/${projectId}/series/standards`, { expected_revision: standards.revision, ...form }), t('Đã lưu chuẩn kỹ thuật của series.'), 'forms')}>{t('Lưu chuẩn kỹ thuật')}</button></div>
      <h4>{t('Trần chi phí tháng · ')}{budget.month}</h4>
      <div className="planner-fields">
        <label className="field">{t('Trần USD / tháng')}<input type="number" min={0} step="0.01" value={caps.usd} onChange={e => setCaps({ ...caps, usd: e.target.value })} /></label>
        <label className="field">{t('Trần CNY / tháng')}<input type="number" min={0} step="0.01" value={caps.cny} onChange={e => setCaps({ ...caps, cny: e.target.value })} /></label>
        <label className="field">{t('Cảnh báo khi dùng đến (%)')}<input type="number" min={1} max={100} value={caps.warn} onChange={e => setCaps({ ...caps, warn: Number(e.target.value) })} /></label>
        <label className="acceptance-confirm"><input type="checkbox" checked={caps.paused} onChange={e => setCaps({ ...caps, paused: e.target.checked })} />{t('Tạm dừng mọi yêu cầu có phí')}</label>
      </div>
      <div className="actions"><button className="button-primary" disabled={busy} onClick={() => act(() => api(`/projects/${projectId}/budget/policy`, { expected_revision: budget.policy.revision, monthly_cap_micro_usd: Math.round(Number(caps.usd) * 1e6), monthly_cap_micro_cny: Math.round(Number(caps.cny) * 1e6), warn_percent: caps.warn, paid_paused: caps.paused }), t('Đã lưu trần chi phí tháng.'), 'forms')}>{t('Lưu trần chi phí')}</button></div>
      {budget.totals.map(entry => <div key={entry.currency} className={`preflight ${entry.exceeded || entry.warning ? 'blocked' : 'ready'} settings-result`}>{entry.currency}: {entry.cap_micro ? t('đã dùng {0} · đang giữ {1} · còn {2} / trần {3}{4}', money(entry.settled_micro), money(entry.reserved_micro), money(entry.remaining_micro), money(entry.cap_micro), entry.exceeded ? t(' · ĐÃ CHẠM TRẦN, app dừng yêu cầu có phí') : entry.warning ? t(' · sắp chạm trần') : '') : t('chưa đặt trần — app không gửi yêu cầu có phí bằng đơn vị này')}</div>)}
      {budget.entries.length > 0 && <><h4>{t('Sổ chi phí')}</h4><div className="table-wrap"><table><thead><tr><th>{t('Thời điểm')}</th><th>{t('Loại')}</th><th>{t('Cổng')}</th><th>{t('Nhà cung cấp')}</th><th>{t('Trạng thái')}</th><th>{t('Giữ')}</th><th>{t('Thực tế')}</th><th></th></tr></thead><tbody>{budget.entries.slice(0, 20).map(e => <tr key={e.id}><td>{e.created_at.slice(0, 16).replace('T', ' ')}</td><td>{ledgerLabel[e.category] ?? e.category}</td><td>{e.gate ? t('Cổng {0}', e.gate) : '—'}</td><td>{e.provider_kind}</td><td><span className={`status ${e.state === 'RESERVED' ? 'warning' : 'neutral'}`}>{stateLabel(e.state)}</span></td><td>{money(e.reserved_micro)} {e.currency}</td><td>{e.actual_micro === null ? '—' : `${money(e.actual_micro)} ${e.currency}`}</td><td className="table-actions">{e.state === 'RESERVED' && <><button disabled={busy || !resolveOk} onClick={() => act(() => api(`/budget/entries/${e.id}/resolve`, { action: 'SETTLE_AT_RESERVED', confirmed: resolveOk }), t('Đã ghi nhận khoản giữ là chi phí thực.'))}>{t('Tính theo mức giữ')}</button><button disabled={busy || !resolveOk} onClick={() => act(() => api(`/budget/entries/${e.id}/resolve`, { action: 'RELEASE', confirmed: resolveOk }), t('Đã nhả khoản giữ.'))}>{t('Nhả khoản giữ')}</button></>}</td></tr>)}</tbody></table></div>
        {budget.entries.some(e => e.state === 'RESERVED') && <label className="acceptance-confirm"><input type="checkbox" checked={resolveOk} onChange={e => setResolveOk(e.target.checked)} />{t('Tôi đã kiểm tra với nhà cung cấp trước khi đóng tay một khoản đang giữ.')}</label>}</>}
      <p className="help">{budget.scope_note}</p>
    </section>
    </div>

    <div className="stack" hidden={step !== 's2'}>
    <section className="panel"><div className="panel-heading"><div><h3>{t('S2 · Nhập kịch bản, liệt kê nhân vật & bối cảnh')}</h3><p>{t('Giữ bản gốc tiếng Việt. Danh sách dưới đây là bản nháp để bạn sửa, gộp tên gọi khác nhau của cùng một nhân vật, rồi mới chọn asset.')}</p></div><span className="count">{mapping.entities.length}{t(' mục')}</span></div>
      <div className="form-row"><div className="field"><span>{t('Kịch bản của tập')}</span><strong>{episodes.find(e => e.id === episodeId)?.name ?? t('Chưa chọn tập ở thanh phía trên')}</strong></div><label className="field">{t('Thời lượng dự kiến (phút)')}<input type="number" min={1} max={120} value={minutes} onChange={e => setMinutes(Number(e.target.value))} /></label></div>
      {!episodes.length && <p className="help">{t('Chưa có tập phim. Thêm tập đầu tiên ở tab Tổng quan.')}</p>}
      <label className="field">{t('Kịch bản tập đầu (tiếng Việt)')}<textarea className="manifest-editor" rows={8} value={text} onChange={e => { setText(e.target.value); setPreview(null) }} placeholder={t('CẢNH 1: Boong tàu - ĐÊM\nTHUYỀN TRƯỞNG: Giữ vững hướng đi.')} /></label>
      <div className="actions"><button disabled={busy || !episodeId || text.trim().length < 40} onClick={() => act(async () => { await api(`/projects/${projectId}/series/screenplay`, { episode_id: episodeId, text, language: 'vi', estimated_seconds: minutes * 60 }); setScreenplays(await api<Screenplay[]>(`/episodes/${episodeId}/screenplays`)); setPreview(null) }, t('Đã lưu phiên bản kịch bản.'))}>{t('Lưu kịch bản')}</button>
        <button disabled={busy || !latestScript || latestScript.text !== text} onClick={() => act(async () => setPreview(await api<ExtractionPreview>(`/projects/${projectId}/series/extraction/preview`, { screenplay_id: latestScript!.id })), t('Đã tính chi phí dự kiến; xem bên dưới trước khi gửi.'))}>{t('Xem chi phí liệt kê')}</button>
        <button className="button-primary" disabled={busy || !preview || !preview.budget.allowed || !latestScript} onClick={() => act(async () => { await api(`/projects/${projectId}/series/extraction/jobs`, { screenplay_id: latestScript!.id, client_request_id: crypto.randomUUID() }); setPreview(null) }, t('Đã xếp việc liệt kê; danh sách tự cập nhật khi xong.'))}>{t('Liệt kê nhân vật & bối cảnh')}</button></div>
      {latestScript && <p className="help">{t('Đang dùng kịch bản phiên bản ')}{latestScript.version}{latestScript.text !== text ? t(' · nội dung trong ô đã sửa, bấm Lưu kịch bản để tạo phiên bản mới') : ''}.</p>}
      {preview && <div className={`preflight ${preview.budget.allowed ? 'ready' : 'blocked'} settings-result`}>{preview.paid ? t('OpenAI {0}: giữ tối đa ${1} cho yêu cầu này.', preview.provider_model, money(preview.estimated_micro)) : preview.note} {preview.budget.reason}</div>}
      {jobs.slice(0, 3).map(j => <div key={j.id} className="voice-job-list"><div><span className={`status ${['BLOCKED', 'RECONCILING'].includes(j.state) ? 'warning' : j.state === 'COMPLETED' ? 'success' : 'neutral'}`}>{stateLabel(j.state)}</span> {{ OPENAI: 'OpenAI', CLAUDE_CODE: 'Claude Code' }[j.provider_kind] ?? t('Mô phỏng')}{j.result?.applied ? t(' · thêm {0}, cập nhật {1}', j.result.applied.created, j.result.applied.updated) : ''}{j.actual_micro ? ` · $${money(j.actual_micro)}` : ''}{j.error && <small className="row-error"> {j.error}</small>}{j.state === 'RECONCILING' && <small>{t(' Kết quả chưa rõ: đóng khoản giữ trong Sổ chi phí ở S1 trước khi gửi lại.')}</small>}</div></div>)}
      {mapping.entities.length ? <div className="table-wrap"><table><thead><tr><th>{t('Loại')}</th><th>{t('Tên · ID ổn định')}</th><th>{t('Tên gọi khác (cách nhau bằng dấu phẩy)')}</th><th>{t('Mô tả ngắn')}</th><th>Scene</th><th>{t('Thao tác')}</th></tr></thead><tbody>{mapping.entities.map(e => {
        const edit = edits[e.id] ?? { name: e.display_name, aliases: e.aliases.join(', '), description: e.description }
        const setEdit = (change: Partial<typeof edit>) => setEdits({ ...edits, [e.id]: { ...edit, ...change } })
        const others = mapping.entities.filter(o => o.kind === e.kind && o.id !== e.id)
        return <tr key={e.id}><td>{kindLabel[e.kind]}</td><td><input value={edit.name} onChange={ev => setEdit({ name: ev.target.value })} /><small><code>{e.entity_key}</code></small></td><td><input value={edit.aliases} onChange={ev => setEdit({ aliases: ev.target.value })} /></td><td><input value={edit.description} onChange={ev => setEdit({ description: ev.target.value })} /></td><td title={e.scenes.join(', ')}>{e.scenes.length}</td><td className="table-actions">
          <button disabled={busy || !edits[e.id]} onClick={() => act(() => api(`/series/entities/${e.id}`, { expected_revision: e.revision, name: edit.name, aliases: edit.aliases.split(',').map(a => a.trim()).filter(Boolean), description: edit.description }), t('Đã lưu mục.'), 'edits')}>{t('Lưu')}</button>
          {others.length > 0 && <><select value={mergeTarget[e.id] ?? ''} onChange={ev => setMergeTarget({ ...mergeTarget, [e.id]: ev.target.value })} aria-label={t('Gộp {0} vào', e.display_name)}><option value="">{t('Gộp vào…')}</option>{others.map(o => <option key={o.id} value={o.id}>{o.display_name}</option>)}</select><button disabled={busy || !mergeTarget[e.id]} onClick={() => act(() => api(`/series/entities/${e.id}/merge`, { source_revision: e.revision, target_id: mergeTarget[e.id], target_revision: others.find(o => o.id === mergeTarget[e.id])!.revision }), t('Đã gộp hai mục; tên cũ thành tên gọi khác.'), 'edits')}>{t('Gộp')}</button></>}
          <button className="button-quiet" disabled={busy} onClick={() => act(() => api(`/series/entities/${e.id}/remove`, { expected_revision: e.revision }), t('Đã bỏ mục khỏi danh sách.'), 'edits')}>{t('Bỏ')}</button></td></tr>
      })}</tbody></table></div> : <p className="help">{t('Chưa có mục nào. Lưu kịch bản rồi bấm Liệt kê, hoặc thêm tay bên dưới.')}</p>}
      <div className="form-row"><label className="field">{t('Thêm tay')}<select value={newEntity.kind} onChange={e => setNewEntity({ ...newEntity, kind: e.target.value })}><option value="character">{t('Nhân vật')}</option><option value="environment">{t('Bối cảnh')}</option></select></label><label className="field grow">{t('Tên')}<input value={newEntity.name} onChange={e => setNewEntity({ ...newEntity, name: e.target.value })} /></label><button disabled={busy || !newEntity.name.trim()} onClick={() => act(async () => { await api(`/projects/${projectId}/series/entities`, { kind: newEntity.kind, name: newEntity.name, aliases: [], description: '' }); setNewEntity({ ...newEntity, name: '' }) }, t('Đã thêm mục.'))}>{t('Thêm')}</button></div>
    </section>
    </div>

    <div className="stack" hidden={step !== 'mapping'}>
    <section className="panel"><div className="panel-heading"><div><h3>{t('S3 · App tự quét asset trong project Unreal')}</h3><p>{t('Đọc Asset Registry của project (chỉ đọc, không sửa, không lưu asset). App viết cho Unreal ')}{scans.target_engine}{t('; phiên bản khác bị chặn.')}</p></div><span className={`status ${latestScan && scans.latest_matches_current_project ? 'success' : activeScan && ['BLOCKED', 'RECONCILING'].includes(activeScan.state) ? 'warning' : 'neutral'}`}>{activeScan ? stateLabel(activeScan.state) : t('Chưa quét')}</span></div>
      <div className="actions"><button className="button-primary" disabled={busy || !unrealConfigured || working} onClick={() => act(() => api(`/projects/${projectId}/series/scans`, {}), t('Đã xếp việc quét; Unreal sẽ mở project để đọc danh sách asset.'))}>{latestScan ? t('Quét lại asset Unreal') : t('Quét asset Unreal')}</button></div>
      {!unrealConfigured && <p className="help">{t('Chưa cấu hình Unreal. Vào Cài đặt → Kết nối Unreal để chọn UnrealEditor-Cmd.exe và tệp .uproject.')}</p>}
      {activeScan?.state === 'RUNNING' && <p className="help">{t('Đang quét… Việc quét dùng chung hàng đợi GPU với render, Kimodo và Foley nên có thể phải chờ việc khác xong.')}</p>}
      {activeScan?.error && <div className="preflight blocked settings-result">{activeScan.error}</div>}
      {activeScan?.state === 'RECONCILING' && <div className="actions"><label className="acceptance-confirm"><input type="checkbox" checked={scanStopped} onChange={e => setScanStopped(e.target.checked)} />{t('Tôi đã đóng cửa sổ Unreal của lần quét bị gián đoạn.')}</label><button disabled={busy || !scanStopped} onClick={() => act(async () => { await api(`/series/scans/${activeScan.id}/release`, { worker_stopped: scanStopped }); setScanStopped(false) }, t('Đã giải phóng hàng đợi; có thể quét lại.'))}>{t('Xác nhận và giải phóng')}</button></div>}
      {latestScan && <><div className="info-line"><span>{t('Lần quét hoàn tất gần nhất')}</span><strong>{latestScan.created_at.slice(0, 16).replace('T', ' ')} · Unreal {latestScan.engine_version} · {latestScan.asset_count} asset</strong></div>
        <p className="help">{Object.entries(latestScan.categories).map(([k, v]) => `${categoryLabel[k] ?? k}: ${v}`).join(' · ')}</p>
        {!scans.latest_matches_current_project && <div className="preflight blocked settings-result">{t('Lần quét này thuộc project Unreal khác với project đang cấu hình (')}{scans.current_ue_project_path || t('chưa cấu hình')}{t('). Quét lại trước khi duyệt CS1.')}</div>}</>}
      <p className="help">{t('Danh sách gồm đường dẫn, loại asset và skeleton. Thumbnail chưa có: bước xuất thumbnail cần kiểm chứng trên Unreal ')}{scans.target_engine}{t(' trước khi bật.')}</p>
    </section>

    <section className="panel"><div className="panel-heading"><div><h3>{t('CS1 · Bạn chọn mapping từ danh sách asset')}</h3><p>{t('Mỗi nhân vật/bối cảnh chọn một asset trong danh sách đã quét; app gợi ý sẵn theo tên. Không có asset phù hợp thì đánh dấu Thiếu để C3 quyết định dùng AI hay bổ sung asset.')}</p></div><span className={`status ${mapping.allowed ? 'success' : 'warning'}`}>{mapping.allowed ? t('Đủ điều kiện duyệt') : t('{0} việc còn thiếu', mapping.blockers.length)}</span></div>
      {!latestScan && <p className="help">{t('Chạy S3 trước để có danh sách asset.')}</p>}
      {mapping.entities.map(e => <div key={e.id} className="series-entity"><div className="series-entity-head"><strong>{e.display_name}</strong><span className="muted">{kindLabel[e.kind]} · <code>{e.entity_key}</code></span><span className={`status ${e.mapping?.status === 'MAPPED' ? 'success' : e.mapping?.status === 'MISSING' ? 'warning' : 'neutral'}`}>{e.mapping ? stateLabel(e.mapping.status) : t('Chưa chọn')}</span></div>
        {e.suggestions && e.suggestions.length > 0 && <p className="help">{t('Gợi ý theo tên: ')}{e.suggestions.map(s => <button key={s.object_path} type="button" className="button-quiet" disabled={busy} title={s.object_path} onClick={() => saveMapping(e, { asset_path: s.object_path })}>{s.asset_name} · {Math.round(s.score * 100)}%</button>)}</p>}
        <AssetPicker projectId={projectId} categories={e.kind === 'character' ? 'character_bp,metahuman,blueprint,skeletal_mesh' : 'map'} label={e.kind === 'character' ? t('BP nhân vật / MetaHuman') : 'Map / Level'} value={e.mapping?.asset_path ?? null} disabled={busy || !latestScan} clearable={false} onPick={path => { if (path) saveMapping(e, { asset_path: path }) }} />
        {e.kind === 'character' && e.mapping?.status === 'MAPPED' && <><AssetPicker projectId={projectId} categories="skeletal_mesh" label={t('Body mesh (skeleton đích cho retarget)')} value={e.mapping.body_mesh_path} disabled={busy} onPick={path => saveMapping(e, { body_mesh_path: path })} /><AssetPicker projectId={projectId} categories="skeletal_mesh" label={t('Face mesh (tùy chọn)')} value={e.mapping.face_mesh_path} disabled={busy} onPick={path => saveMapping(e, { face_mesh_path: path })} /></>}
        <div className="actions"><button disabled={busy || e.mapping?.status === 'MISSING'} onClick={() => saveMapping(e, { status: 'MISSING' })}>{t('Đánh dấu Thiếu')}</button></div>
      </div>)}

      <h4>{t('Thư mục animation và VFX của series')}</h4>
      {folders.animation.length || folders.niagara.length ? <div className="layout-two"><div><p className="help">{t('Animation — chọn thư mục app được phép dùng khi gán chuyển động.')}</p><ul className="folder-tree">{folders.animation.map(f => <li key={f.path} style={{ paddingLeft: f.depth * 16 }}><label><input type="checkbox" checked={picked.animation.includes(f.path)} onChange={() => setPicked({ ...picked, animation: toggle(picked.animation, f.path) })} />{f.path.split('/').pop()} <small>({f.count})</small></label></li>)}</ul></div><div><p className="help">Niagara VFX.</p><ul className="folder-tree">{folders.niagara.map(f => <li key={f.path} style={{ paddingLeft: f.depth * 16 }}><label><input type="checkbox" checked={picked.vfx.includes(f.path)} onChange={() => setPicked({ ...picked, vfx: toggle(picked.vfx, f.path) })} />{f.path.split('/').pop()} <small>({f.count})</small></label></li>)}</ul></div></div> : <p className="help">{t('Chưa có thư mục animation/VFX trong lần quét.')}</p>}

      {mapping.retarget_pairs.length > 0 && <><h4>{t('Retarget theo skeleton nguồn')}</h4><div className="table-wrap"><table><thead><tr><th>{t('Skeleton nguồn của animation')}</th><th>{t('Số clip')}</th><th>{t('Nhân vật đích')}</th><th>{t('Cách xử lý')}</th></tr></thead><tbody>{mapping.retarget_pairs.map(p => { const key = `${p.source_skeleton}|${p.target_entity_key}`; return <tr key={key}><td title={p.source_skeleton}>{short(p.source_skeleton) === '—' ? t('Không rõ skeleton') : short(p.source_skeleton)}</td><td>{p.animation_count}</td><td>{characters.find(c => c.entity_key === p.target_entity_key)?.display_name ?? p.target_entity_key}</td><td>{p.mode === 'DIRECT' ? t('Cùng skeleton — gắn thẳng') : <select value={picked.decisions[key] ?? ''} onChange={e => setPicked({ ...picked, decisions: { ...picked.decisions, [key]: e.target.value } })}><option value="">{t('Chưa chọn')}</option>{mapping.retargeter_assets.map(r => <option key={r.object_path} value={r.object_path}>{t('Dùng ')}{r.asset_name}</option>)}<option value="NEEDS_CREATE">{t('Chưa có — app tạo retargeter ở phase dựng UE')}</option></select>}</td></tr> })}</tbody></table></div></>}
      <div className="actions"><button disabled={busy || !latestScan} onClick={() => act(() => api(`/projects/${projectId}/series/folders`, { expected_revision: mapping.draft.revision, animation_folders: picked.animation, vfx_folders: picked.vfx, retargeters: decisionsPayload() }), t('Đã lưu thư mục và quyết định retarget.'), 'forms')}>{t('Lưu thư mục & retarget')}</button></div>
      {mapping.pending_unreal_work > 0 && <p className="help">{mapping.pending_unreal_work}{t(' retargeter được đánh dấu cần tạo. App chưa ghi vào Unreal ở bước này; việc tạo và duyệt retargeter thực hiện trong phase dựng UE.')}</p>}

      {mapping.blockers.length > 0 && <div className="preflight blocked settings-result"><strong>{t('Chưa duyệt được CS1:')}</strong><ul>{mapping.blockers.map(b => <li key={b}>{b}</li>)}</ul></div>}
      <div className="actions"><button className="button-primary" disabled={busy || !mapping.allowed || Boolean(mapping.library && !mapping.library.draft_changed)} onClick={() => act(() => api(`/projects/${projectId}/series/cs1/approve`, { expected_draft_hash: mapping.draft_hash }), t('Đã duyệt CS1 và đăng ký version thư viện mới.'))}>{t('Duyệt CS1 → đăng ký thư viện v')}{(mapping.library?.version ?? 0) + 1}</button></div>
      {mapping.library && <p className="help">{t('Thư viện v')}{mapping.library.version}{t(' duyệt lúc ')}{mapping.library.created_at.slice(0, 16).replace('T', ' ')}. {mapping.library.draft_changed ? t('Danh sách hoặc mapping đã đổi sau lần duyệt; duyệt lại để tăng version.') : t('Bản đang xem trùng bản đã duyệt.')}{t(' Duyệt CS1 xác nhận lựa chọn mapping, chưa chứng minh asset chạy đúng trong shot.')}</p>}
    </section>
    </div>

    <div className="stack" hidden={step !== 'style'}>
    <StyleBiblePanel projectId={projectId} refreshKey={`${mapping.draft_hash}:${mapping.library?.version ?? 0}`} unrealConfigured={unrealConfigured} onStatus={setBible} />
    </div>
  </div>
}
