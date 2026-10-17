import { useCallback, useEffect, useState } from 'react'
import { api, type FlowState } from './api'
import StepTabs, { type StepDef } from './StepTabs'
import { errorLabel, stateLabel } from './uiText'
import RoutePanel from './RoutePanel'
import VoiceTimingPanel from './VoiceTimingPanel'
import { t } from './i18n'

type Named = { name: string; entity_key: string | null; effective_key?: string | null }
type ShotCharacter = Named & { action: string; motion_tags: string[] }
type Line = { line_key: string | null; character: Named; text: string; text_en: string; effective_key?: string | null }
type ShotT = { shot_key: string | null; shot_id?: string; description: string; description_en: string; shot_size: string; camera_note: string; duration_seconds: number; face_visible: boolean; content_flags: string[]; vfx_tags: string[]; prompt_en: string; styled_prompt?: string; characters: ShotCharacter[]; dialogue: Line[] }
type SceneT = { scene_key: string; heading: string; summary: string; environment: Named; shots: ShotT[]; scene_hash: string; duration_seconds: number; issues: string[]; warnings: string[]; approved: boolean }
type LibraryEntity = { id: string; entity_key: string; kind: string; display_name: string }
type Current = { revision: { version: number; source: string; created_at: string; content_hash: string; library_version: number; style_version: number } | null; scenes: SceneT[]; library: LibraryEntity[]; c1: { exact_version: number; current: boolean } | null; all_scenes_approved: boolean; summary: { scenes: number; shots: number; duration_seconds: number } }
type OpenStatus = { checks: { key: string; label: string; ok: boolean; detail: string }[]; ready: boolean; blockers: string[]; last_scan_at: string | null; scan_diff: { compared: boolean; added_count: number; removed_count: number; added: { object_path: string; asset_name: string; category: string }[] } }
type Job = { id: string; state: string; provider_kind: string; actual_micro: number | null; revision_version: number | null; error: string | null }
type Preview = { provider_kind: string; provider_model: string | null; paid: boolean; estimated_micro: number; budget: { allowed: boolean; reason: string | null }; note: string | null }
type Screenplay = { id: string; version: number; text: string }
type CompareItem = { kind: 'character' | 'environment'; names: string[]; entity_key: string | null; entity_id: string | null; display_name: string; status: string; asset_path: string | null; in_approved_library: boolean; scenes: string[]; shot_count: number }
type Compare = { revision_version: number | null; items: CompareItem[]; missing: { assets: CompareItem[]; motions: { tag: string; shots: string[] }[]; vfx: { tag: string; shots: string[] }[] }; folders_selected?: { animation: boolean; vfx: boolean }; library: { version: number; draft_changed: boolean }; style: { version: number; draft_changed: boolean; warnings: string[] }; has_new_resources: boolean; blockers: string[]; allowed: boolean; expected_hash: string | null; c2: { sequence: number; created_at: string; current: boolean; decision: { skipped: boolean; library_version: number; style_version: number; added_entities: string[] } } | null; motion_note: string }

const sizes: Record<string, string> = { EXTREME_WIDE: t('Toàn cảnh rất rộng'), WIDE: t('Toàn cảnh'), MEDIUM: t('Trung cảnh'), CLOSE_UP: t('Cận cảnh'), EXTREME_CLOSE_UP: t('Đặc tả'), OVER_SHOULDER: t('Qua vai'), TWO_SHOT: t('Hai nhân vật'), INSERT: t('Chèn chi tiết') }
const flags: Record<string, string> = { VIOLENCE: t('Bạo lực'), BLOOD: t('Máu me') }
const sourceLabel: Record<string, string> = { LOCAL_PLANNER_FIXTURE: t('mô phỏng'), OPENAI: 'OpenAI', CLAUDE_API: 'Claude API', CLAUDE_CODE: 'Claude Code', MANUAL_EDIT: t('bạn sửa') }
const paidLabel: Record<string, string> = { OPENAI: 'OpenAI', CLAUDE_API: 'Claude API' }
const statusLabel: Record<string, string> = { NEW: t('Mới · chưa có trong thư viện'), UNMAPPED: t('Chưa chọn asset'), MAPPED: t('Đã chọn asset'), MISSING: t('Thiếu asset') }
const money = (micro: number) => (micro / 1e6).toFixed(4)
const tags = (text: string) => text.split(',').map(entry => entry.trim().toLowerCase()).filter(Boolean)
const blankShot = (): ShotT => ({ shot_key: null, description: '', description_en: '', shot_size: 'MEDIUM', camera_note: '', duration_seconds: 3, face_visible: false, content_flags: [], vfx_tags: [], prompt_en: '', characters: [], dialogue: [] })

function SceneEditor({ scene, dirty, characters, environments, busy, onChange, onSave, onReset, onApprove, onDelete, onMove }: { scene: SceneT; dirty: boolean; characters: string[]; environments: string[]; busy: boolean; onChange: (scene: SceneT) => void; onSave: () => void; onReset: () => void; onApprove: () => void; onDelete: () => void; onMove: (direction: 'up' | 'down') => void }) {
  const setShot = (index: number, change: Partial<ShotT>) => onChange({ ...scene, shots: scene.shots.map((s, i) => i === index ? { ...s, ...change } : s) })
  const lists = `scene-${scene.scene_key}`
  return <details className="scene-card">
    <summary><strong>{scene.scene_key} · {scene.heading}</strong><span className="muted">{scene.shots.length} shot · {scene.duration_seconds.toFixed(1)}{t(' giây · ')}{scene.environment.name || t('chưa có bối cảnh')}</span><span className={`status ${dirty ? 'neutral' : scene.approved ? 'success' : scene.issues.length ? 'warning' : 'neutral'}`}>{dirty ? t('Đang sửa · chưa lưu') : scene.approved ? t('Đã duyệt') : scene.issues.length ? t('Cần sửa') : t('Cần duyệt')}</span></summary>
    <datalist id={`${lists}-chars`}>{characters.map(n => <option key={n} value={n} />)}</datalist>
    <datalist id={`${lists}-envs`}>{environments.map(n => <option key={n} value={n} />)}</datalist>
    <div className="planner-fields">
      <label className="field">{t('Tiêu đề scene')}<input value={scene.heading} onChange={e => onChange({ ...scene, heading: e.target.value })} /></label>
      <label className="field">{t('Bối cảnh ')}{scene.environment.effective_key ? <small>{t('khớp thư viện: ')}{scene.environment.effective_key}</small> : <small>{t('chưa có trong thư viện')}</small>}<input list={`${lists}-envs`} value={scene.environment.name} onChange={e => onChange({ ...scene, environment: { name: e.target.value, entity_key: null } })} /></label>
    </div>
    <label className="field">{t('Tóm tắt')}<textarea rows={2} value={scene.summary} onChange={e => onChange({ ...scene, summary: e.target.value })} /></label>
    {scene.shots.map((shot, index) => <details key={shot.shot_key ?? `new-${index}`} className="shot-card">
      <summary><strong>{shot.shot_id ?? t('Shot mới')}</strong><span className="muted">{sizes[shot.shot_size]} · {shot.duration_seconds}{t(' giây · ')}{shot.description.slice(0, 70) || t('chưa có mô tả')}</span></summary>
      <label className="field">{t('Mô tả hành động (tiếng Việt)')}<textarea rows={2} value={shot.description} onChange={e => setShot(index, { description: e.target.value })} /></label>
      <label className="field">{t('Bản dịch tiếng Anh')}<input value={shot.description_en} onChange={e => setShot(index, { description_en: e.target.value })} /></label>
      <div className="planner-fields">
        <label className="field">{t('Cỡ cảnh')}<select value={shot.shot_size} onChange={e => setShot(index, { shot_size: e.target.value })}>{Object.entries(sizes).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label className="field">{t('Thời lượng dự kiến (giây)')}<input type="number" min={0.5} max={60} step={0.5} value={shot.duration_seconds} onChange={e => setShot(index, { duration_seconds: Number(e.target.value) })} /></label>
        <label className="field">{t('Ghi chú máy quay')}<input value={shot.camera_note} onChange={e => setShot(index, { camera_note: e.target.value })} /></label>
        <label className="field">{t('Nhãn VFX (tiếng Anh, cách nhau bằng dấu phẩy)')}<input value={shot.vfx_tags.join(', ')} onChange={e => setShot(index, { vfx_tags: tags(e.target.value) })} placeholder="rain, smoke" /></label>
      </div>
      <div className="actions"><label className="acceptance-confirm"><input type="checkbox" checked={shot.face_visible} onChange={e => setShot(index, { face_visible: e.target.checked })} />{t('Lộ mặt nhân vật')}</label>{Object.entries(flags).map(([value, label]) => <label key={value} className="acceptance-confirm"><input type="checkbox" checked={shot.content_flags.includes(value)} onChange={e => setShot(index, { content_flags: e.target.checked ? [...shot.content_flags, value] : shot.content_flags.filter(f => f !== value) })} />{label}</label>)}</div>
      <label className="field">{t('Prompt hình ảnh tiếng Anh (không ghi từ style; app tự nối cụm từ của Style bible)')}<textarea rows={2} value={shot.prompt_en} onChange={e => setShot(index, { prompt_en: e.target.value })} /></label>
      {!dirty && shot.styled_prompt && <p className="help">{t('Prompt sau khi nối style: ')}<code>{shot.styled_prompt}</code></p>}
      <h5>{t('Nhân vật trong shot')}</h5>
      {shot.characters.map((c, ci) => <div key={ci} className="form-row"><label className="field">{t('Tên ')}{c.effective_key && <small>{c.effective_key}</small>}<input list={`${lists}-chars`} value={c.name} onChange={e => setShot(index, { characters: shot.characters.map((x, i) => i === ci ? { ...x, name: e.target.value, entity_key: null } : x) })} /></label><label className="field grow">{t('Hành động')}<input value={c.action} onChange={e => setShot(index, { characters: shot.characters.map((x, i) => i === ci ? { ...x, action: e.target.value } : x) })} /></label><label className="field">{t('Nhãn motion (tiếng Anh)')}<input value={c.motion_tags.join(', ')} onChange={e => setShot(index, { characters: shot.characters.map((x, i) => i === ci ? { ...x, motion_tags: tags(e.target.value) } : x) })} placeholder="walk, turn" /></label><button type="button" className="button-quiet" onClick={() => setShot(index, { characters: shot.characters.filter((_, i) => i !== ci) })}>{t('Bỏ')}</button></div>)}
      <button type="button" onClick={() => setShot(index, { characters: [...shot.characters, { name: '', entity_key: null, action: '', motion_tags: [] }] })}>{t('Thêm nhân vật')}</button>
      <h5>{t('Lời thoại')}</h5>
      {shot.dialogue.map((line, li) => <div key={line.line_key ?? `new-${li}`} className="form-row"><label className="field">{t('Người nói')}<input list={`${lists}-chars`} value={line.character.name} onChange={e => setShot(index, { dialogue: shot.dialogue.map((x, i) => i === li ? { ...x, character: { name: e.target.value, entity_key: null } } : x) })} /></label><label className="field grow">{t('Thoại gốc')}<input value={line.text} onChange={e => setShot(index, { dialogue: shot.dialogue.map((x, i) => i === li ? { ...x, text: e.target.value } : x) })} /></label><label className="field grow">{t('Bản dịch tiếng Anh')}<input value={line.text_en} onChange={e => setShot(index, { dialogue: shot.dialogue.map((x, i) => i === li ? { ...x, text_en: e.target.value } : x) })} /></label><button type="button" className="button-quiet" onClick={() => setShot(index, { dialogue: shot.dialogue.filter((_, i) => i !== li) })}>{t('Bỏ')}</button></div>)}
      <div className="actions"><button type="button" onClick={() => setShot(index, { dialogue: [...shot.dialogue, { line_key: null, character: { name: '', entity_key: null }, text: '', text_en: '' }] })}>{t('Thêm lời thoại')}</button><button type="button" className="button-quiet" disabled={scene.shots.length <= 1} onClick={() => onChange({ ...scene, shots: scene.shots.filter((_, i) => i !== index) })}>{t('Xóa shot này')}</button></div>
    </details>)}
    <div className="actions">
      <button type="button" onClick={() => onChange({ ...scene, shots: [...scene.shots, blankShot()] })}>{t('Thêm shot')}</button>
      <button className="button-primary" disabled={busy || !dirty} onClick={onSave}>{t('Lưu scene (tạo phiên bản mới)')}</button>
      <button className="button-quiet" disabled={busy || !dirty} onClick={onReset}>{t('Bỏ thay đổi')}</button>
      <button disabled={busy || dirty || scene.approved || scene.issues.length > 0} onClick={onApprove}>{t('Duyệt scene')}</button>
      <button className="button-quiet" disabled={busy || dirty} onClick={() => onMove('up')}>{t('Lên')}</button>
      <button className="button-quiet" disabled={busy || dirty} onClick={() => onMove('down')}>{t('Xuống')}</button>
      <button className="button-quiet" disabled={busy || dirty} onClick={onDelete}>{t('Xóa scene')}</button>
    </div>
    {!dirty && scene.issues.map(i => <p key={i} className="row-error">{i}</p>)}
    {!dirty && scene.warnings.map(w => <p key={w} className="help">{w}</p>)}
  </details>
}

export const episodeSteps: StepDef[] = [
  { id: 'breakdown', code: '01–03 · C1', label: t('Kịch bản & breakdown') },
  { id: 'library', code: '04 · C2', label: t('Thư viện của tập') },
  { id: 'route', code: '05 · C3', label: t('Phương thức & chi phí') },
  { id: 'timing', code: '06 · C4', label: t('Thoại & timing') },
]

export default function EpisodePage({ projectId, episodeId, unrealConfigured, step, onStep, marks, onOpenSeries }: { projectId: string; episodeId: string; unrealConfigured: boolean; step: string; onStep: (step: string) => void; marks: Record<string, FlowState>; onOpenSeries: () => void }) {
  const [status, setStatus] = useState<OpenStatus | null>(null)
  const [current, setCurrent] = useState<Current | null>(null)
  const [compare, setCompare] = useState<Compare | null>(null)
  const [jobs, setJobs] = useState<Job[]>([])
  const [screenplays, setScreenplays] = useState<Screenplay[]>([])
  const [text, setText] = useState('')
  const [minutes, setMinutes] = useState(10)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [drafts, setDrafts] = useState<Record<string, SceneT>>({})
  const [newScene, setNewScene] = useState({ heading: '', environment: '' })
  const [aliasTarget, setAliasTarget] = useState<Record<string, string>>({})
  const [exported, setExported] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [routeVersion, setRouteVersion] = useState(0)

  const load = useCallback(async () => {
    if (!episodeId) return
    const [open, breakdown, comparison, jobList] = await Promise.all([
      api<OpenStatus>(`/episodes/${episodeId}/open`), api<Current>(`/episodes/${episodeId}/breakdown`),
      api<Compare>(`/episodes/${episodeId}/compare`), api<Job[]>(`/episodes/${episodeId}/breakdown/jobs`)])
    setStatus(open); setCurrent(breakdown); setCompare(comparison); setJobs(jobList)
  }, [episodeId])

  useEffect(() => {
    setDrafts({}); setPreview(null); setExported(''); setError(''); setNotice(''); setStatus(null); setCurrent(null); setCompare(null)
    if (!episodeId) { setScreenplays([]); return }
    load().catch(e => setError(errorLabel(e)))
    api<Screenplay[]>(`/episodes/${episodeId}/screenplays`).then(list => { setScreenplays(list); setText(list[0]?.text ?? '') }).catch(e => setError(errorLabel(e)))
  }, [episodeId])
  const working = jobs.some(j => ['QUEUED', 'RUNNING'].includes(j.state))
  useEffect(() => {
    if (!working) return
    const timer = setInterval(() => load().catch(e => setError(errorLabel(e))), 2500)
    return () => clearInterval(timer)
  }, [working, load])

  async function act(action: () => Promise<unknown>, message: string) {
    setBusy(true); setError(''); setNotice('')
    try { await action(); await load(); setNotice(message) } catch (e) { setError(errorLabel(e)) } finally { setBusy(false) }
  }

  if (!projectId) return <div className="empty"><span className="empty-icon" aria-hidden="true">◇</span><strong>{t('Chưa chọn dự án')}</strong><p>{t('Chọn dự án ở thanh phía trên để làm việc với từng tập.')}</p></div>
  if (!episodeId) return <div className="empty"><span className="empty-icon" aria-hidden="true">◇</span><strong>{t('Chưa chọn tập')}</strong><p>{t('Chọn tập đang làm ở thanh phía trên, hoặc thêm tập ở tab Tổng quan.')}</p></div>
  const latestScript = screenplays[0]
  const hash = current?.revision?.content_hash ?? null
  const characterNames = current?.library.filter(e => e.kind === 'character').map(e => e.display_name) ?? []
  const environmentNames = current?.library.filter(e => e.kind === 'environment').map(e => e.display_name) ?? []
  const dropDraft = (key: string) => setDrafts(Object.fromEntries(Object.entries(drafts).filter(([k]) => k !== key)))

  return <div className="stack v45 episode-page">
    {error && <div className="alert error" role="alert">{error}</div>}
    {notice && <div className="alert notice" role="status">{notice}</div>}
    <StepTabs steps={episodeSteps} value={step} onChange={onStep} marks={marks} label={t('Các bước của Khu A')} />

    {episodeId && status && current && compare && <>
      <div className="stack" hidden={step !== 'breakdown'}>
      <section className="panel"><div className="panel-heading"><div><h3>{t('01 · Mở project và nạp thư viện series')}</h3><p>{t('Kiểm tra kết nối Unreal, phiên bản, chuẩn FPS và nạp version mới nhất của thư viện mapping cùng Style bible.')}</p></div><span className={`status ${status.ready ? 'success' : 'warning'}`}>{status.ready ? t('Sẵn sàng') : t('Chưa sẵn sàng')}</span></div>
        <div className="review-list">{status.checks.map(c => <div key={c.key} className="review-row"><div><strong>{c.label}</strong><span>{c.detail}</span></div><span className={`status ${c.ok ? 'success' : 'warning'}`}>{c.ok ? t('Đạt') : t('Chưa đạt')}</span></div>)}</div>
        <div className="actions"><button disabled={busy || !unrealConfigured} onClick={() => act(() => api(`/projects/${projectId}/series/scans`, {}), t('Đã xếp việc quét lại asset Unreal; xem tiến độ ở Thư viện series → S3.'))}>{t('Quét lại asset Unreal mới thêm')}</button><button className="button-quiet" onClick={onOpenSeries}>{t('Mở Thư viện series')}</button></div>
        <p className="help">{status.last_scan_at ? t('Lần quét hoàn tất gần nhất: {0}. ', status.last_scan_at.slice(0, 16).replace('T', ' ')) : t('Chưa có lần quét nào. ')}{status.scan_diff.compared ? t('So với lần trước: thêm {0} asset, mất {1} asset{2}.', status.scan_diff.added_count, status.scan_diff.removed_count, status.scan_diff.added_count ? ` (${status.scan_diff.added.slice(0, 6).map(a => a.asset_name).join(', ')}${status.scan_diff.added_count > 6 ? '…' : ''})` : '') : t('Cần hai lần quét để biết asset mới thêm.')}</p>
        {!status.ready && <div className="preflight blocked settings-result">{status.blockers.join(' ')}{t(' Hoàn tất ở tab Thư viện series trước khi phân tích tập.')}</div>}
      </section>

      <section className="panel"><div className="panel-heading"><div><h3>{t('02 · Nhập kịch bản của tập')}</h3><p>{t('Giữ bản gốc tiếng Việt. Tập đầu dùng luôn kịch bản đã nhập ở S2; mỗi lần lưu tạo một phiên bản mới.')}</p></div><span className="count">{latestScript ? t('phiên bản {0}', latestScript.version) : t('chưa có')}</span></div>
        <div className="form-row"><label className="field">{t('Thời lượng dự kiến (phút)')}<input type="number" min={1} max={120} value={minutes} onChange={e => setMinutes(Number(e.target.value))} /></label></div>
        <label className="field">{t('Nội dung kịch bản')}<textarea rows={8} value={text} onChange={e => { setText(e.target.value); setPreview(null) }} /></label>
        <div className="actions"><button disabled={busy || text.trim().length < 40 || latestScript?.text === text} onClick={() => act(async () => { await api(`/projects/${projectId}/series/screenplay`, { episode_id: episodeId, text, language: 'vi', estimated_seconds: minutes * 60 }); setScreenplays(await api<Screenplay[]>(`/episodes/${episodeId}/screenplays`)); setPreview(null) }, t('Đã lưu phiên bản kịch bản.'))}>{t('Lưu kịch bản')}</button></div>
      </section>

      <section className="panel"><div className="panel-heading"><div><h3>{t('03 · Phân tích và chia scene / shot')}</h3><p>{t('AI chia scene, shot, dịch sang tiếng Anh, khớp nhân vật và bối cảnh với thư viện, viết prompt theo Style bible và ước lượng thời lượng. Mỗi scene, shot có ID ổn định.')}</p></div><span className="count">{current.summary.scenes} scene · {current.summary.shots} shot</span></div>
        <div className="actions"><button disabled={busy || !status.ready || !latestScript || latestScript.text !== text} onClick={() => act(async () => setPreview(await api<Preview>(`/episodes/${episodeId}/breakdown/preview`, { screenplay_id: latestScript!.id })), t('Đã tính chi phí dự kiến; xem bên dưới trước khi gửi.'))}>{t('Xem chi phí phân tích')}</button><button className="button-primary" disabled={busy || working || !preview || !preview.budget.allowed || !latestScript} onClick={() => act(async () => { await api(`/episodes/${episodeId}/breakdown/jobs`, { screenplay_id: latestScript!.id, client_request_id: crypto.randomUUID() }); setPreview(null) }, t('Đã xếp việc phân tích; breakdown tự cập nhật khi xong.'))}>{current.revision ? t('Phân tích lại (giữ scene đã duyệt)') : t('Phân tích kịch bản')}</button></div>
        {preview && <div className={`preflight ${preview.budget.allowed ? 'ready' : 'blocked'} settings-result`}>{preview.paid && t('{0} {1}: giữ tối đa ${2} cho yêu cầu này.', paidLabel[preview.provider_kind] ?? preview.provider_kind, preview.provider_model, money(preview.estimated_micro))} {preview.note} {preview.budget.reason}</div>}
        {jobs.slice(0, 3).map(j => <div key={j.id} className="voice-job-list"><div><span className={`status ${['BLOCKED', 'RECONCILING'].includes(j.state) ? 'warning' : j.state === 'COMPLETED' ? 'success' : 'neutral'}`}>{stateLabel(j.state)}</span> {sourceLabel[j.provider_kind] ?? j.provider_kind}{j.revision_version ? ` · breakdown v${j.revision_version}` : ''}{j.actual_micro ? ` · $${money(j.actual_micro)}` : ''}{j.error && <small className="row-error"> {j.error}</small>}{j.state === 'RECONCILING' && <small>{t(' Kết quả chưa rõ: đóng khoản giữ trong Sổ chi phí (Thư viện series → S1) trước khi gửi lại.')}</small>}</div></div>)}
        <p className="help">{t('Phân tích lại chỉ thay các scene chưa duyệt; scene đã duyệt và scene bạn tự thêm được giữ nguyên, ID không đổi.')}</p>
      </section>

      <section className="panel"><div className="panel-heading"><div><h3>{t('C1 · Bạn review, sửa và chốt breakdown')}</h3><p>{t('Duyệt theo scene; mở từng shot khi cần. Kiểm tra nhân vật và bối cảnh được khớp đúng với thư viện.')}</p></div><span className={`status ${current.c1?.current ? 'success' : current.all_scenes_approved ? 'neutral' : 'warning'}`}>{current.c1?.current ? t('Đã chốt · breakdown v{0}', current.c1.exact_version) : current.c1 ? t('Đã đổi sau lần chốt') : t('Chưa chốt')}</span></div>
        {current.revision ? <p className="help">Breakdown v{current.revision.version} ({sourceLabel[current.revision.source] ?? current.revision.source}) · {current.summary.duration_seconds.toFixed(1)}{t(' giây dự kiến · viết theo thư viện v')}{current.revision.library_version}{t(' và Style bible v')}{current.revision.style_version}.</p> : <p className="help">{t('Chưa có breakdown. Chạy bước 03 hoặc tự thêm scene bên dưới.')}</p>}
        {current.scenes.map(scene => { const draft = drafts[scene.scene_key]; return <SceneEditor key={scene.scene_key + scene.scene_hash} scene={draft ?? scene} dirty={Boolean(draft)} characters={characterNames} environments={environmentNames} busy={busy}
          onChange={next => setDrafts({ ...drafts, [scene.scene_key]: next })} onReset={() => dropDraft(scene.scene_key)}
          onSave={() => act(async () => { await api(`/episodes/${episodeId}/breakdown/scenes/save`, { expected_content_hash: hash, scene: draft }); dropDraft(scene.scene_key) }, t('Đã lưu {0}; scene này cần duyệt lại.', scene.scene_key))}
          onApprove={() => act(() => api(`/episodes/${episodeId}/breakdown/scenes/${scene.scene_key}/approve`, { expected_scene_hash: scene.scene_hash }), t('Đã duyệt {0}.', scene.scene_key))}
          onDelete={() => act(() => api(`/episodes/${episodeId}/breakdown/scenes/${scene.scene_key}/delete`, { expected_content_hash: hash }), t('Đã xóa {0}; ID này không được dùng lại.', scene.scene_key))}
          onMove={direction => act(() => api(`/episodes/${episodeId}/breakdown/scenes/${scene.scene_key}/move`, { expected_content_hash: hash, direction }), t('Đã đổi thứ tự scene.'))} /> })}
        <div className="form-row"><label className="field grow">{t('Thêm scene — tiêu đề')}<input value={newScene.heading} onChange={e => setNewScene({ ...newScene, heading: e.target.value })} placeholder={t('CẢNH 3: Cầu cảng - SÁNG')} /></label><label className="field">{t('Bối cảnh')}<input value={newScene.environment} onChange={e => setNewScene({ ...newScene, environment: e.target.value })} /></label><button disabled={busy || !status.ready || !newScene.heading.trim() || !newScene.environment.trim()} onClick={() => act(async () => { await api(`/episodes/${episodeId}/breakdown/scenes/add`, { expected_content_hash: hash, after_scene_key: null, heading: newScene.heading, environment_name: newScene.environment }); setNewScene({ heading: '', environment: '' }) }, t('Đã thêm scene ở cuối tập.'))}>{t('Thêm scene')}</button></div>
        <div className="actions"><button className="button-primary" disabled={busy || !current.all_scenes_approved || Boolean(current.c1?.current) || Object.keys(drafts).length > 0} onClick={() => act(() => api(`/episodes/${episodeId}/breakdown/c1/approve`, { expected_content_hash: hash }), t('Đã chốt breakdown của tập (C1).'))}>{t('Chốt breakdown tập (C1)')}</button></div>
        <p className="help">{t('Chốt C1 chỉ xác nhận nội dung. Chưa có shot nào được dựng; phương thức UE/AI/Hybrid chọn ở C3. Sửa breakdown sau khi đã chốt sẽ mở lại C1 và các bước phía sau.')}</p>
      </section>
      </div>

      <div className="stack" hidden={step !== 'library'}>
      <section className="panel"><div className="panel-heading"><div><h3>{t('04 · Đối chiếu tập với thư viện')}</h3><p>{t('Nhân vật và bối cảnh của tập so với thư viện series. Mục mới được thêm vào thư viện rồi chọn asset như ở CS1; mục không có asset được đánh dấu Thiếu.')}</p></div><span className="count">{compare.items.length}{t(' mục dùng trong tập')}</span></div>
        {compare.items.length ? <div className="table-wrap"><table><thead><tr><th>{t('Loại')}</th><th>{t('Tên trong tập')}</th><th>{t('Thư viện')}</th><th>{t('Trạng thái')}</th><th>Scene · shot</th><th>{t('Thao tác')}</th></tr></thead><tbody>{compare.items.map(i => { const key = `${i.kind}:${i.display_name}`; const targets = current.library.filter(e => e.kind === i.kind); return <tr key={key}><td>{i.kind === 'character' ? t('Nhân vật') : t('Bối cảnh')}</td><td className="strong">{i.names.join(', ')}</td><td>{i.entity_key ? <><code>{i.entity_key}</code>{!i.in_approved_library && <small>{t(' · chờ duyệt C2')}</small>}</> : '—'}</td><td><span className={`status ${i.status === 'MAPPED' ? 'success' : 'warning'}`}>{statusLabel[i.status] ?? i.status}</span></td><td>{i.scenes.length} scene{i.kind === 'character' ? ` · ${i.shot_count} shot` : ''}</td><td className="table-actions">{i.status === 'NEW' && <><button disabled={busy} onClick={() => act(() => api(`/episodes/${episodeId}/compare/add`, { kind: i.kind, name: i.display_name }), t('Đã thêm {0} vào thư viện; chọn asset ở Thư viện series → CS1.', i.display_name))}>{t('Thêm vào thư viện')}</button>{targets.length > 0 && <><select value={aliasTarget[key] ?? ''} onChange={e => setAliasTarget({ ...aliasTarget, [key]: e.target.value })} aria-label={t('{0} là tên gọi khác của', i.display_name)}><option value="">{t('Là tên gọi khác của…')}</option>{targets.map(entry => <option key={entry.id} value={entry.id}>{entry.display_name}</option>)}</select><button disabled={busy || !aliasTarget[key]} onClick={() => act(() => api(`/episodes/${episodeId}/compare/alias`, { name: i.display_name, entity_id: aliasTarget[key] }), t('Đã ghi nhận tên gọi khác.'))}>{t('Gán')}</button></>}</>}{i.status === 'UNMAPPED' && <button onClick={onOpenSeries}>{t('Chọn asset ở CS1')}</button>}</td></tr> })}</tbody></table></div> : <p className="help">{t('Chưa có breakdown để đối chiếu.')}</p>}
        <h4>{t('Danh sách thiếu')}</h4>
        <p className="help">Asset: {compare.missing.assets.length ? compare.missing.assets.map(i => `${i.display_name} (${statusLabel[i.status] ?? i.status})`).join(' · ') : t('không thiếu')}.</p>
        <p className="help">Motion: {compare.folders_selected && !compare.folders_selected.animation ? t('chưa chọn thư mục animation ở CS1 nên chưa đối chiếu') : compare.missing.motions.length ? compare.missing.motions.map(m => `${m.tag} (${m.shots.length} shot)`).join(' · ') : t('không thấy thiếu')}.</p>
        <p className="help">VFX: {compare.folders_selected && !compare.folders_selected.vfx ? t('chưa chọn thư mục VFX ở CS1 nên chưa đối chiếu') : compare.missing.vfx.length ? compare.missing.vfx.map(m => `${m.tag} (${m.shots.length} shot)`).join(' · ') : t('không thấy thiếu')}.</p>
        <p className="help">{compare.motion_note}</p>
        <div className="actions"><button disabled={busy || !compare.revision_version} onClick={() => act(async () => { const result = await api<{ relative_path: string }>(`/episodes/${episodeId}/compare/export`, {}); setExported(result.relative_path) }, t('Đã xuất danh sách thiếu vào thư mục dự án.'))}>{t('Xuất danh sách thiếu (CSV)')}</button><button className="button-quiet" onClick={onOpenSeries}>{t('Mở Thư viện series để chọn asset, reference và giọng')}</button></div>
        {exported && <p className="help">File: <code>{exported}</code>{t(' trong thư mục dự án.')}</p>}
      </section>

      <section className="panel"><div className="panel-heading"><div><h3>{t('C2 · Duyệt phần bổ sung → cập nhật thư viện')}</h3><p>{t('Duyệt mapping, retargeter và reference của tài nguyên mới rồi đăng ký vào thư viện series (tăng version). Không có tài nguyên mới thì ghi nhận bỏ qua.')}</p></div><span className={`status ${compare.c2?.current ? 'success' : compare.allowed ? 'neutral' : 'warning'}`}>{compare.c2?.current ? (compare.c2.decision.skipped ? t('Đã ghi nhận · không có tài nguyên mới') : t('Đã duyệt · thư viện v{0}', compare.c2.decision.library_version)) : compare.c2 ? t('Đã đổi sau lần duyệt') : t('Chưa duyệt')}</span></div>
        <div className="info-line"><span>{t('Thư viện · Style bible đang dùng')}</span><strong>v{compare.library.version}{compare.library.draft_changed ? t(' (nháp đã đổi)') : ''} · v{compare.style.version}{compare.style.draft_changed ? t(' (nháp đã đổi)') : ''}</strong></div>
        {compare.blockers.length > 0 && <div className="preflight blocked settings-result"><strong>{t('Chưa duyệt được C2:')}</strong><ul>{compare.blockers.map(b => <li key={b}>{b}</li>)}</ul></div>}
        {compare.style.warnings.length > 0 && <div className="preflight ready settings-result"><ul>{compare.style.warnings.map(w => <li key={w}>{w}</li>)}</ul></div>}
        <div className="actions"><button className="button-primary" disabled={busy || !compare.allowed || Boolean(compare.c2?.current)} onClick={() => act(() => api(`/episodes/${episodeId}/c2/approve`, { expected_hash: compare.expected_hash }), compare.has_new_resources ? t('Đã duyệt C2: thư viện và Style bible được đăng ký version mới.') : t('Đã ghi nhận C2: tập không có tài nguyên mới.'))}>{compare.has_new_resources ? t('Duyệt C2 → tăng version thư viện') : t('Ghi nhận C2 · không có tài nguyên mới')}</button></div>
        {compare.c2 && <p className="help">{t('Lần duyệt gần nhất: ')}{compare.c2.created_at.slice(0, 16).replace('T', ' ')}{compare.c2.decision.added_entities.length ? t(' · bổ sung {0}', compare.c2.decision.added_entities.join(', ')) : ''}.</p>}
      </section>
      </div>

      <div className="stack" hidden={step !== 'route'}>
      <RoutePanel projectId={projectId} episodeId={episodeId} refreshKey={`${hash}:${compare.c2?.sequence ?? 0}:${compare.c2?.current ?? false}:${compare.library.version}:${compare.style.version}`} onChanged={() => setRouteVersion(v => v + 1)} />
      </div>
      <div className="stack" hidden={step !== 'timing'}>
      <VoiceTimingPanel episodeId={episodeId} refreshKey={`${hash}:${compare.c2?.sequence ?? 0}:${compare.c2?.current ?? false}:${compare.style.version}:${routeVersion}`} />
      </div>
    </>}
  </div>
}
