import { useCallback, useEffect, useState } from 'react'
import { api, type AISettings, type Episode } from './api'
import { errorLabel, routeLabel, subsystemLabel } from './uiText'

type Screenplay = { id: string; version: number; language: string; target_language: string; estimated_seconds: number; text: string; created_at: string }
type PlanningJob = { id: string; screenplay_id: string; state: string; provider_kind: string; operation_id: string; provider_request_id: string | null; attempt: number; max_attempts: number; estimated_cost_microusd: number; error: string | null; draft_id: string | null; attempts: { attempt: number; state: string; validation_error: string | null; provider_model: string | null }[] }
type Issue = { shot_id: string; kind: string; message: string }
type DraftShot = { scene_id: string; manifest: { shot_id: string; timeline: { in: number; out_exclusive: number }; route: { suggested: string }; performances: { performance_id: string; dialogue_id?: string }[] }; action_intent: string; cinematography_intent: string; dialogue_translation_drafts: { performance_id: string; source_text: string; draft_text: string; source_language: string; target_language: string; source_kind: string }[]; validation: { allowed: boolean; issues: Issue[] } }
type PlanningDraft = { id: string; version: number; provider_kind: string; provider_model: string | null; actual_cost_microusd: number; draft: { scenes: { scene_id: string; heading: string; action_summary: string }[]; shots: DraftShot[]; issues: Issue[]; unresolved_requirements: string[]; valid_for_apply: boolean; total_duration_frames: number; expected_duration_frames: number }; applications: { shot_code: string; shot_revision: number }[] }
type ApplyPreview = { shot_code: string; target_revision: number; allowed: boolean; blockers: string[]; impact: { changed_fields: string[]; affected_subsystems: string[]; stale_artifacts: unknown[] }; manifest_before: Record<string, unknown> | null; manifest_after: Record<string, unknown>; preview_token: string; requires_c1_approval: boolean; production_jobs_enqueued: number }
type Grant = { id: string; provider_kind: string; max_cost_microusd: number; reserved_microusd: number; spent_microusd: number; expires_at: string }

const jobLabel: Record<string, string> = { QUEUED: 'Đang chờ', RUNNING: 'Đang tạo nháp', DRAFT_READY: 'Có bản nháp', BLOCKED: 'Bị chặn', RECONCILING: 'Cần đối soát' }
const money = (microusd: number) => `$${(microusd / 1_000_000).toFixed(4)}`

export default function PlannerPage({ projectId, episodes, onApplied }: { projectId: string; episodes: Episode[]; onApplied: () => Promise<void> }) {
  const [episodeId, setEpisodeId] = useState('')
  const [screenplays, setScreenplays] = useState<Screenplay[]>([])
  const [jobs, setJobs] = useState<PlanningJob[]>([])
  const [drafts, setDrafts] = useState<PlanningDraft[]>([])
  const [grants, setGrants] = useState<Grant[]>([])
  const [aiSettings, setAISettings] = useState<AISettings | null>(null)
  const [grantId, setGrantId] = useState('')
  const [scriptText, setScriptText] = useState('')
  const [language, setLanguage] = useState('vi')
  const [targetLanguage, setTargetLanguage] = useState('vi')
  const [seconds, setSeconds] = useState(90)
  const [screenplayId, setScreenplayId] = useState('')
  const [fpsNum, setFpsNum] = useState(24)
  const [fpsDen, setFpsDen] = useState(1)
  const [draftId, setDraftId] = useState('')
  const [shotCode, setShotCode] = useState('')
  const [preview, setPreview] = useState<ApplyPreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [grantUsd, setGrantUsd] = useState('1')
  const [grantExpiry, setGrantExpiry] = useState('')

  const load = useCallback(async () => {
    if (!projectId) return
    const [newJobs, newGrants, settings] = await Promise.all([
      api<PlanningJob[]>(`/projects/${projectId}/planning/jobs`),
      api<Grant[]>(`/projects/${projectId}/planning/grants`),
      api<AISettings>('/settings/ai')
    ])
    setJobs(newJobs); setGrants(newGrants); setAISettings(settings)
    if (episodeId) {
      const [newScripts, newDrafts] = await Promise.all([
        api<Screenplay[]>(`/episodes/${episodeId}/screenplays`),
        api<PlanningDraft[]>(`/episodes/${episodeId}/planning/drafts`)
      ])
      setScreenplays(newScripts); setDrafts(newDrafts)
    }
  }, [projectId, episodeId])

  useEffect(() => { setEpisodeId(''); setScreenplays([]); setJobs([]); setDrafts([]); setGrants([]); setScreenplayId(''); setDraftId(''); setShotCode(''); setPreview(null) }, [projectId])
  useEffect(() => { setScreenplayId(''); setDraftId(''); setShotCode(''); setPreview(null); load().catch(e => setError(errorLabel(e))) }, [load])
  useEffect(() => {
    if (!projectId) return
    const stream = new EventSource(`/api/events/stream?project_id=${encodeURIComponent(projectId)}`)
    stream.onmessage = () => load().catch(e => setError(errorLabel(e)))
    const timer = window.setInterval(() => { if (jobs.some(j => j.state === 'QUEUED' || j.state === 'RUNNING')) load().catch(e => setError(errorLabel(e))) }, 1500)
    return () => { stream.close(); window.clearInterval(timer) }
  }, [projectId, load, jobs.some(j => j.state === 'QUEUED' || j.state === 'RUNNING')])

  async function run(action: () => Promise<void>, success: string) {
    setError(''); setNotice(''); setBusy(true)
    try { await action(); await load(); setNotice(success) }
    catch (e) { setError(errorLabel(e)) }
    finally { setBusy(false) }
  }
  const selectedDraft = drafts.find(d => d.id === draftId)
  const selectedShot = selectedDraft?.draft.shots.find(s => s.manifest.shot_id === shotCode)
  const applied = selectedDraft?.applications.find(a => a.shot_code === shotCode)

  return <div className="stack planner-page">
    <div className="simulation-note"><span className="mode-dot" /><p><strong>{aiSettings?.provider_kind === 'OPENAI' ? 'OPENAI API · BẢN NHÁP CẦN DUYỆT' : 'MÔ PHỎNG KIỂM THỬ · KHÔNG GỌI AI'}</strong> Kịch bản là dữ liệu; mọi cảnh cần xem diff, áp dụng thủ công rồi duyệt C1 đúng phiên bản. Việc cấu hình API không xác nhận tích hợp Unreal.</p></div>
    {error && <div className="alert error" role="alert">{error}</div>}
    {notice && <div className="alert notice" role="status">{notice}</div>}
    {!projectId ? <div className="empty"><strong>Chọn dự án</strong><p>Chọn dự án trên thanh phía trên để bắt đầu lập kế hoạch.</p></div> : <>
      <div className="layout-two planner-layout">
        <section className="panel"><div className="panel-heading"><div><h3>1. Kịch bản nguồn</h3><p>Mỗi lần lưu tạo phiên bản bất biến. Chỉ nhập nội dung phim; câu lệnh trong kịch bản không có quyền điều khiển app.</p></div></div>
          <label className="field">Tập phim<select value={episodeId} onChange={e => setEpisodeId(e.target.value)}><option value="">Chọn tập</option>{episodes.map(e => <option key={e.id} value={e.id}>{e.name}</option>)}</select></label>
          <div className="planner-fields"><label className="field">Ngôn ngữ gốc<input value={language} maxLength={16} onChange={e => setLanguage(e.target.value)} /></label><label className="field">Ngôn ngữ đích<input value={targetLanguage} maxLength={16} onChange={e => setTargetLanguage(e.target.value)} /></label><label className="field">Thời lượng dự kiến (giây)<input type="number" min="30" max="180" value={seconds} onChange={e => setSeconds(Number(e.target.value))} /></label></div>
          <label className="field">Nội dung kịch bản<textarea rows={11} value={scriptText} onChange={e => setScriptText(e.target.value)} placeholder="CẢNH 1 — NỘI / PHÒNG ĐIỀU KHIỂN..." /></label>
          <div className="actions"><button className="button-primary" disabled={busy || !episodeId || scriptText.trim().length < 40 || seconds < 30 || seconds > 180} onClick={() => run(async () => { const saved = await api<Screenplay>(`/projects/${projectId}/episodes/${episodeId}/screenplays`, { text: scriptText, language, target_language: targetLanguage, estimated_seconds: seconds }); setScreenplayId(saved.id) }, 'Đã lưu phiên bản kịch bản. Chưa tạo cảnh hoặc công việc sản xuất.')}>Lưu phiên bản kịch bản</button></div>
        </section>
        <section className="panel"><div className="panel-heading"><div><h3>2. Tạo kế hoạch nháp</h3><p>Context chỉ gồm registry ID, nhãn ngữ nghĩa và capability; không truyền asset path hoặc khóa bí mật.</p></div><span className="status warning">{aiSettings?.provider_kind === 'OPENAI' ? 'OPENAI API' : 'MÔ PHỎNG'}</span></div>
          <label className="field">Phiên bản kịch bản<select value={screenplayId} onChange={e => { setScreenplayId(e.target.value); setDraftId(''); setPreview(null) }}><option value="">Chọn phiên bản</option>{screenplays.map(s => <option key={s.id} value={s.id}>v{s.version} · {s.estimated_seconds} giây · {s.language} → {s.target_language}</option>)}</select></label>
          <div className="planner-fields"><label className="field">FPS tử số<input type="number" min="1" value={fpsNum} onChange={e => setFpsNum(Number(e.target.value))} /></label><label className="field">FPS mẫu số<input type="number" min="1" value={fpsDen} onChange={e => setFpsDen(Number(e.target.value))} /></label></div>
          {aiSettings?.provider_kind === 'OPENAI' && <label className="field">Hạn mức đã cấp cho OpenAI<select value={grantId} onChange={e => setGrantId(e.target.value)}><option value="">Chọn hạn mức</option>{grants.filter(g => g.provider_kind === 'OPENAI' && new Date(g.expires_at) > new Date() && g.max_cost_microusd > g.spent_microusd + g.reserved_microusd).map(g => <option key={g.id} value={g.id}>{money(g.max_cost_microusd - g.spent_microusd - g.reserved_microusd)} còn lại · {new Date(g.expires_at).toLocaleString('vi-VN')}</option>)}</select></label>}
          <div className="actions"><button className="button-primary" disabled={busy || !screenplayId || fpsNum < 1 || fpsDen < 1 || (aiSettings?.provider_kind === 'OPENAI' && (!aiSettings.key_configured || !grantId))} onClick={() => run(async () => { await api(`/screenplays/${screenplayId}/planning/jobs`, { screenplay_id: screenplayId, client_request_id: crypto.randomUUID(), fps: { num: fpsNum, den: fpsDen }, grant_id: aiSettings?.provider_kind === 'OPENAI' ? grantId : null }) }, 'Đã xếp việc tạo nháp. Trang sẽ cập nhật từ cơ sở dữ liệu.')}>{aiSettings?.provider_kind === 'OPENAI' ? 'Gọi OpenAI tạo nháp' : 'Tạo nháp mô phỏng'}</button><button disabled={busy || !screenplayId || !draftId || (aiSettings?.provider_kind === 'OPENAI' && (!aiSettings.key_configured || !grantId))} onClick={() => run(async () => { await api(`/screenplays/${screenplayId}/planning/jobs`, { screenplay_id: screenplayId, client_request_id: crypto.randomUUID(), fps: { num: fpsNum, den: fpsDen }, grant_id: aiSettings?.provider_kind === 'OPENAI' ? grantId : null, base_draft_id: draftId }) }, 'Đã xếp việc tạo lại. Nội dung thủ công và khóa sẽ được kiểm tra khi áp dụng.')}>Tạo lại từ bản nháp đã chọn</button></div>
          <p className="help">{aiSettings?.provider_kind === 'OPENAI' ? `Model ${aiSettings.model}. Lời gọi OpenAI có phí và chỉ bắt đầu sau thao tác Gọi OpenAI tạo nháp; không tự gửi lại khi kết quả chưa rõ.` : 'Bản mô phỏng chỉ thử luồng điều khiển, chi phí $0 và không có kết quả AI thật.'}</p>
          <div className="planner-job-list">{jobs.filter(j => !episodeId || screenplays.some(s => s.id === j.screenplay_id)).slice(0, 5).map(j => <div key={j.id}><strong>{jobLabel[j.state] ?? j.state}</strong><span>{j.provider_kind} · lần {j.attempt}/{j.max_attempts} · dự trù tối đa {money(j.estimated_cost_microusd)}</span>{j.error && <small>{j.error}</small>}{j.state === 'RECONCILING' && <small>Cần đối soát với OpenAI trước khi tạo lại. Mã thao tác: {j.operation_id}. Request ID: {j.provider_request_id || 'chưa nhận'}.</small>}{j.attempts.filter(a => a.validation_error).map(a => <small key={a.attempt}>Lần {a.attempt}: {a.validation_error}</small>)}</div>)}</div>
        </section>
      </div>

      <section className="panel"><div className="panel-heading"><div><h3>3. Xem bản nháp và tác động</h3><p>Chọn một shot, xem thay đổi so với phiên bản hiện hành rồi áp dụng. Tuyến chỉ là đề xuất.</p></div><span className="count">{drafts.length} bản nháp</span></div>
        <div className="planner-fields"><label className="field">Bản nháp<select value={draftId} onChange={e => { setDraftId(e.target.value); setShotCode(''); setPreview(null) }}><option value="">Chọn bản nháp</option>{drafts.map(d => <option key={d.id} value={d.id}>v{d.version} · {d.provider_kind} · {d.draft.shots.length} cảnh · {d.draft.valid_for_apply ? 'có thể xem apply' : 'cần giải quyết'}</option>)}</select></label><label className="field">Cảnh trong bản nháp<select value={shotCode} onChange={e => { setShotCode(e.target.value); setPreview(null) }} disabled={!selectedDraft}><option value="">Chọn cảnh</option>{selectedDraft?.draft.shots.map(s => <option key={s.manifest.shot_id} value={s.manifest.shot_id}>{s.manifest.shot_id} · [{s.manifest.timeline.in}, {s.manifest.timeline.out_exclusive})</option>)}</select></label></div>
        {selectedDraft && <><div className="planner-summary"><span className={selectedDraft.draft.valid_for_apply ? 'status success' : 'status warning'}>{selectedDraft.draft.valid_for_apply ? 'Hợp lệ để xem apply' : 'Có yêu cầu chưa giải quyết'}</span><span>{selectedDraft.draft.scenes.length} scene · {selectedDraft.draft.shots.length} shot</span><span>Thời lượng {selectedDraft.draft.total_duration_frames}/{selectedDraft.draft.expected_duration_frames} frame</span>{selectedDraft.provider_model && <span>Model response: {selectedDraft.provider_model}</span>}<span>Dự tính từ usage {money(selectedDraft.actual_cost_microusd)}</span></div>
          {selectedDraft.draft.scenes.map(scene => <p className="planner-scene" key={scene.scene_id}><strong>{scene.heading}</strong> {scene.action_summary}</p>)}
          {selectedDraft.draft.unresolved_requirements.length > 0 && <div className="preflight blocked"><strong>Cần giải quyết:</strong> {selectedDraft.draft.unresolved_requirements.join(' · ')}</div>}
          {selectedDraft.draft.issues.length > 0 && <div className="planner-issues">{selectedDraft.draft.issues.map((issue, index) => <p key={index}>{issue.shot_id}: {issue.message}</p>)}</div>}
        </>}
        {selectedShot && <div className="planner-shot"><h4>{shotCode} {applied && <span className="status success">Đã áp dụng v{applied.shot_revision}</span>}</h4><div className="planner-summary"><span>[{selectedShot.manifest.timeline.in}, {selectedShot.manifest.timeline.out_exclusive})</span><span>Tuyến đề xuất: {routeLabel(selectedShot.manifest.route.suggested)}</span><span>{selectedShot.manifest.performances.length} performance</span></div><p><strong>Hành động:</strong> {selectedShot.action_intent}</p><p><strong>Máy quay:</strong> {selectedShot.cinematography_intent}</p>{selectedShot.dialogue_translation_drafts.map(t => <p key={t.performance_id}><strong>Thoại {t.performance_id}:</strong> {t.source_text} → {t.draft_text || 'Cần bản dịch để duyệt'} <small>({t.source_kind})</small></p>)}
          <div className="actions"><button disabled={busy} onClick={() => run(async () => { setPreview(await api<ApplyPreview>(`/planning/drafts/${draftId}/shots/${shotCode}/preview`)) }, 'Đã kiểm tra diff và tác động theo trạng thái hiện hành.')}>Xem diff & tác động</button><button className="button-primary" disabled={busy || !preview?.allowed || !!applied} onClick={() => run(async () => { await api(`/planning/drafts/${draftId}/shots/${shotCode}/apply`, { shot_code: shotCode, expected_target_revision: preview!.target_revision, preview_token: preview!.preview_token }); setPreview(null); await onApplied() }, 'Đã áp dụng bản nháp. Hãy sang Duyệt để duyệt C1 đúng phiên bản; chưa có công việc sản xuất.')}>Áp dụng shot đã xem</button></div>
          {preview && <div className={`impact ${preview.allowed ? '' : 'planner-blocked'}`}><h4>{preview.allowed ? 'Có thể áp dụng' : 'Chưa thể áp dụng'} · revision hiện hành {preview.target_revision}</h4><p>Trường đổi: {preview.impact.changed_fields.join(', ') || 'không có'}</p><p>Thành phần ảnh hưởng: {preview.impact.affected_subsystems.map(subsystemLabel).join(', ') || 'không có'}</p><p>Artifact sẽ thành STALE: {preview.impact.stale_artifacts.length}</p><p>Approval C1: cần duyệt riêng · Công việc sản xuất được xếp: {preview.production_jobs_enqueued}</p>{preview.blockers.map((blocker, index) => <p key={index} className="row-error">{blocker}</p>)}<details className="planner-diff"><summary>Xem manifest trước và sau</summary><div><section><strong>Hiện hành</strong><pre>{preview.manifest_before ? JSON.stringify(preview.manifest_before, null, 2) : 'Chưa có shot'}</pre></section><section><strong>Bản nháp</strong><pre>{JSON.stringify(preview.manifest_after, null, 2)}</pre></section></div></details></div>}
        </div>}
      </section>
      <section className="panel"><div className="panel-heading"><div><h3>Ngân sách Planning API</h3><p>Cấp hạn mức riêng trước khi gọi OpenAI; việc lưu hạn mức chưa gửi request.</p></div><span className="status warning">OPENAI · CÓ PHÍ</span></div><p className="help">Khóa API được nhập trong Cài đặt qua cửa sổ Windows. Grant chỉ là giới hạn trong ứng dụng, không thay thế giới hạn chi tiêu của tài khoản OpenAI.</p>
        <div className="planner-fields planner-grant"><label className="field">Nhà cung cấp<input value="OpenAI" readOnly /></label><label className="field">Hạn mức USD<input type="number" min="0.000001" step="0.01" value={grantUsd} onChange={e => setGrantUsd(e.target.value)} /></label><label className="field">Hết hạn theo giờ máy<input type="datetime-local" value={grantExpiry} onChange={e => setGrantExpiry(e.target.value)} /></label><button disabled={busy || !grantExpiry || !Number.isFinite(Number(grantUsd)) || Number(grantUsd) <= 0} onClick={() => run(async () => { await api(`/projects/${projectId}/planning/grants`, { provider_kind: 'OPENAI', max_cost_microusd: Math.round(Number(grantUsd) * 1_000_000), expires_at: new Date(grantExpiry).toISOString() }) }, 'Đã lưu hạn mức planning. Chưa gửi request hoặc tạo tài nguyên.')}>Lưu hạn mức</button></div>
        {grants.length > 0 && <div className="table-wrap settings-result"><table><thead><tr><th>Provider</th><th>Hạn mức</th><th>Đã dùng</th><th>Đang giữ</th><th>Hết hạn</th></tr></thead><tbody>{grants.map(g => <tr key={g.id}><td>{g.provider_kind}</td><td>{money(g.max_cost_microusd)}</td><td>{money(g.spent_microusd)}</td><td>{money(g.reserved_microusd)}</td><td>{g.expires_at}</td></tr>)}</tbody></table></div>}
      </section>
    </>}
  </div>
}
