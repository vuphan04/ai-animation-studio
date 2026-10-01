import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import type { Episode, Job } from './api'
import { errorLabel } from './uiText'

type Case = { id: string; title: string; execution: string; priority: string }
type Catalog = { cases: Case[]; actions: string[]; cameras: string[]; faults: string[] }
type Run = { id: string; version: number; evidence_kind: string; validity: string; d1_metadata_matches: boolean; created_at: string; snapshot: { build_sha256: string; profile_sha256: string }; records: { version: number; kind: string; payload: Record<string, unknown> }[] }
type Recovery = { id: string; kind: string; state: string; error: string | null; result: Record<string, unknown> | null }
type Settings = { revision: number; backup_parent: string; restore_parent: string }
type Gate = { status: string; blockers: string[] }
type Baseline = { version: number; payload: { limits: Record<string, number>; currency: string; evidence: { path: string } } }
const metricLabels: Record<string, string> = { assemble_seconds: 'Lắp dựng (giây)', render_seconds: 'Render (giây)', generate_seconds: 'Generate (giây)', vram_peak_mib: 'VRAM cao nhất (MiB)', ram_peak_mib: 'RAM cao nhất (MiB)', manual_actions: 'Số thao tác tay', cloud_actual: 'Chi phí cloud thực tế' }

export default function AcceptancePage({ projectId, episodes }: { projectId: string; episodes: Episode[] }) {
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [runs, setRuns] = useState<Run[]>([])
  const [runId, setRunId] = useState('')
  const [episodeId, setEpisodeId] = useState('')
  const [scope, setScope] = useState('LOCAL_CONTRACT')
  const [gate, setGate] = useState<Gate | null>(null)
  const [settings, setSettings] = useState<Settings>({ revision: 0, backup_parent: '', restore_parent: '' })
  const [recovery, setRecovery] = useState<Recovery[]>([])
  const [jobs, setJobs] = useState<Job[]>([])
  const [closed, setClosed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [tc, setTc] = useState('TC-49')
  const [tier, setTier] = useState('UNIT_CONTRACT')
  const [status, setStatus] = useState('BLOCKED')
  const [path, setPath] = useState('')
  const [notes, setNotes] = useState('')
  const [cacheMode, setCacheMode] = useState('COLD')
  const [currency, setCurrency] = useState('USD')
  const [metrics, setMetrics] = useState<Record<string, string>>({})
  const [baseline, setBaseline] = useState('')
  const [savedBaseline, setSavedBaseline] = useState<Baseline | null>(null)
  const [limits, setLimits] = useState<Record<string, string>>({})
  const [baselineConfirmed, setBaselineConfirmed] = useState(false)
  const [scores, setScores] = useState<Record<string, boolean>>({})
  const [decision, setDecision] = useState('REJECT')
  const [wrongActors, setWrongActors] = useState('')
  const [overwrites, setOverwrites] = useState('')
  const selected = runs.find(r => r.id === runId)
  const reload = useCallback(async () => {
    const [c, s, r] = await Promise.all([api<Catalog>('/acceptance/catalog'), api<Settings>('/settings/recovery'), api<Recovery[]>('/recovery/jobs')])
    setCatalog(c); setSettings(previous => previous.revision === s.revision && (previous.backup_parent || previous.restore_parent) ? previous : s); setRecovery(r)
    if (projectId) {
      const [list, g, j, b] = await Promise.all([api<Run[]>(`/projects/${projectId}/acceptance/runs`), api<Gate>(`/projects/${projectId}/acceptance/gate`), api<Job[]>(`/projects/${projectId}/jobs`), api<Baseline | null>(`/projects/${projectId}/acceptance/baseline`)])
      setRuns(list); setGate(g); setJobs(j); setSavedBaseline(b)
    } else { setRuns([]); setGate(null); setJobs([]); setSavedBaseline(null) }
  }, [projectId])
  useEffect(() => { setRunId(''); setEpisodeId(''); setScores({}); reload().catch(e => setError(errorLabel(e))) }, [reload])
  useEffect(() => { if (!recovery.some(j => ['QUEUED', 'RUNNING'].includes(j.state))) return; const id = window.setInterval(() => reload().catch(e => setError(errorLabel(e))), 2500); return () => window.clearInterval(id) }, [recovery, reload])
  async function work(action: () => Promise<unknown>, text: string) {
    setBusy(true); setError(''); setMessage('')
    try { await action(); await reload(); setMessage(text) } catch (e) { setError(errorLabel(e)) } finally { setBusy(false) }
  }
  async function browseFolder(field: 'backup_parent' | 'restore_parent') { const result = await api<{ path: string }>('/settings/recovery/browse', {}); setSettings(s => ({ ...s, [field]: result.path })) }
  async function browseEvidence(setter: (value: string) => void) { const value = await api<{ relative_path: string }>(`/projects/${projectId}/acceptance/browse`, {}); setter(value.relative_path) }
  function record(kind: string, payload: Record<string, unknown>) { return api(`/acceptance/runs/${runId}/records`, { expected_version: selected!.version, kind, payload }) }
  return <div className="acceptance-page">
    {error && <div className="alert error" role="alert">{error}</div>}{message && <p role="status" className="help">{message}</p>}
    <section className="panel"><div className="panel-heading"><div><h3>Nghiệm thu & độ bền</h3><p>Hồ sơ run, evidence và sao lưu. Tạo hồ sơ không chạy pilot, không gọi cloud và không tự cấp approval sản xuất.</p></div><span className="status warning">P7 · BLOCKED</span></div>
      {gate?.blockers.map(b => <p className="help" key={b}>{b}</p>)}
      <div className="actions"><button disabled={busy} onClick={() => work(reload, 'Đã tải trạng thái từ DB.')}>Tải lại</button></div>
    </section>
    <section className="panel"><div className="panel-heading"><div><h3>Baseline P0 trước nghiệm thu</h3><p>Chốt giới hạn từ số đo P0 trên máy thật; lưu phiên bản và evidence trước run. Việc nhập ngưỡng không chứng minh phép đo đã thực hiện.</p></div></div>
      <p className="help">{savedBaseline ? `Đã lưu baseline v${savedBaseline.version} · ${savedBaseline.payload.currency} · ${savedBaseline.payload.evidence.path}` : 'Chưa có baseline đã chốt. Run thật bị chặn.'}</p>
      <div className="planner-fields">{Object.entries(metricLabels).map(([key, label]) => <label className="field" key={key}>Giới hạn · {label}<input type="number" min="0" value={limits[key] ?? ''} onChange={e => setLimits(m => ({ ...m, [key]: e.target.value }))} placeholder={savedBaseline ? String(savedBaseline.payload.limits[key]) : ''} /></label>)}<label className="field">Đơn vị phí<select value={currency} onChange={e => setCurrency(e.target.value)}><option>USD</option><option>CNY</option></select></label></div>
      <div className="setting-file"><label className="field grow">Evidence phép đo P0<input value={baseline} onChange={e => setBaseline(e.target.value)} /></label><button disabled={busy || !projectId} onClick={() => work(() => browseEvidence(setBaseline), 'Đã chọn baseline.')}>Duyệt…</button></div>
      <label className="acceptance-confirm"><input type="checkbox" checked={baselineConfirmed} onChange={e => setBaselineConfirmed(e.target.checked)} />Tôi đã chốt các ngưỡng theo phép đo P0 trước nghiệm thu.</label>
      <button disabled={busy || !projectId || !baselineConfirmed || !baseline || Object.keys(metricLabels).some(k => !limits[k]?.trim())} onClick={() => work(() => api(`/projects/${projectId}/acceptance/baseline`, { expected_version: savedBaseline?.version ?? 0, limits: Object.fromEntries(Object.keys(metricLabels).map(k => [k, Number(limits[k])])), currency, evidence_path: baseline, confirmed: baselineConfirmed }), 'Đã pin baseline mới; các run cũ giữ đúng baseline đã chốt.')}>Chốt baseline phiên bản mới</button>
    </section>
    <section className="panel"><div className="panel-heading"><div><h3>Hồ sơ pilot D1 · 6 cảnh / 48 giây</h3><p>Lưu build/profile, manifest, receipt và kiểm kê render hiện có. Hồ sơ môi trường thật vẫn cần file kết quả và người review.</p></div></div>
      <div className="planner-fields"><label className="field">Tập phim<select value={episodeId} onChange={e => setEpisodeId(e.target.value)}><option value="">Chọn tập</option>{episodes.map(e => <option key={e.id} value={e.id}>{e.name}</option>)}</select></label><label className="field">Loại run<select value={scope} onChange={e => setScope(e.target.value)}><option value="LOCAL_CONTRACT">Local · kiểm thử hợp đồng</option><option value="REAL_INTEGRATION">Môi trường thật · chưa xác minh</option></select></label></div>
      <div className="actions"><button disabled={busy || !projectId || !episodeId} onClick={() => work(async () => { const r = await api<Run>(`/projects/${projectId}/acceptance/runs`, { episode_id: episodeId, evidence_kind: scope }); setRunId(r.id) }, 'Đã lưu snapshot bất biến; chưa xác nhận đã chạy pilot.')}>Tạo hồ sơ run</button></div>
      <label className="field">Run<select value={runId} onChange={e => { setRunId(e.target.value); setScores({}) }}><option value="">Chọn run</option>{runs.map(r => <option key={r.id} value={r.id}>{r.id.slice(0, 12)} · {r.evidence_kind} · v{r.version} · {r.validity}</option>)}</select></label>
      {selected && <><p className="help">Build {selected.snapshot.build_sha256.slice(0, 16)} · profile {selected.snapshot.profile_sha256.slice(0, 16)} · metadata D1: {selected.d1_metadata_matches ? 'đúng 6 cảnh / 48 giây' : 'chưa khớp'} · {selected.validity}</p>
        <div className="planner-fields"><label className="field">Test case<select value={tc} onChange={e => setTc(e.target.value)}>{catalog?.cases.map(c => <option key={c.id} value={c.id}>{c.id} · {c.title}</option>)}</select></label><label className="field">Evidence<select value={tier} onChange={e => setTier(e.target.value)}><option value="UNIT_CONTRACT">Unit / contract</option><option value="INTEGRATION">UE / provider thật</option><option value="UAT">Người dùng review</option></select></label><label className="field">Kết quả báo cáo<select value={status} onChange={e => setStatus(e.target.value)}><option>BLOCKED</option><option>FAIL</option><option>PASS</option></select></label></div>
        <div className="setting-file"><label className="field grow">File evidence trong project root<input value={path} onChange={e => setPath(e.target.value)} placeholder="logs/run-01.json" /></label><button disabled={busy} onClick={() => work(() => browseEvidence(setPath), 'Đã chọn evidence.')}>Duyệt…</button></div>
        <label className="field">Ghi chú / lỗi cần sửa<textarea value={notes} onChange={e => setNotes(e.target.value)} placeholder="Không nhập key, token hoặc mật khẩu." /></label>
        <div className="actions"><button disabled={busy} onClick={() => work(() => record('CASE', { tc, tier, status, paths: path.trim() ? [path.trim()] : [], notes }), 'Đã thêm evidence có version; kết quả nhập tay chưa đóng gate.')}>Ghi kết quả TC</button><button disabled={busy} onClick={() => work(() => api(`/acceptance/runs/${runId}/export`, { expected_version: selected.version }), 'Đã xếp xuất evidence package. Xem kết quả trong hàng đợi bên dưới.')}>Xuất evidence package</button></div>
        <div className="table-wrap"><table><thead><tr><th>Version</th><th>Loại</th><th>Kết quả / phạm vi</th></tr></thead><tbody>{selected.records.map(r => <tr key={r.version}><td>{r.version}</td><td>{r.kind}</td><td>{String(r.payload.tc ?? r.payload.cache_mode ?? r.payload.decision)} · {String(r.payload.status ?? r.payload.provenance ?? 'LOCAL_USER')}</td></tr>)}</tbody></table></div>
      </>}
    </section>
    {selected && <><section className="panel"><div className="panel-heading"><div><h3>Đo hiệu năng và chi phí</h3><p>Nhập số đo thực tế từ log/công cụ máy UE. Chưa có bộ đo RAM/VRAM tự động; baseline phải chốt trước nghiệm thu.</p></div></div>
      <div className="planner-fields"><label className="field">Cache<select value={cacheMode} onChange={e => setCacheMode(e.target.value)}><option>COLD</option><option>WARM</option></select></label><label className="field">Đơn vị phí<select value={currency} onChange={e => setCurrency(e.target.value)}><option>USD</option><option>CNY</option></select></label>{Object.entries(metricLabels).map(([key, label]) => <label className="field" key={key}>{label}<input type="number" min="0" value={metrics[key] ?? ''} onChange={e => setMetrics(m => ({ ...m, [key]: e.target.value }))} /></label>)}</div>
      <div className="setting-file"><label className="field grow">Evidence baseline P0 đã chốt<input value={baseline} onChange={e => setBaseline(e.target.value)} /></label><button disabled={busy} onClick={() => work(() => browseEvidence(setBaseline), 'Đã chọn baseline.')}>Duyệt…</button></div>
      <button disabled={busy || !baseline || Object.keys(metricLabels).some(k => !metrics[k]?.trim())} onClick={() => work(() => record('MEASUREMENTS', { cache_mode: cacheMode, currency, baseline_path: baseline, ...Object.fromEntries(Object.keys(metricLabels).map(k => [k, Number(metrics[k])])) }), 'Đã ghi số đo của người vận hành.')}>Lưu số đo</button>
    </section><section className="panel"><div className="panel-heading"><div><h3>Review chất lượng riêng</h3><p>Chấm đủ 20 action và 10 camera trước polish. Đạt khi ≥17/20 và ≥8/10; approval này chỉ thuộc hồ sơ nghiệm thu.</p></div></div>
      <div className="layout-two">{(['actions', 'cameras'] as const).map(group => <div key={group}><h4>{group === 'actions' ? 'Hành động' : 'Máy quay'}</h4><div className="acceptance-rubric">{catalog?.[group].map(id => <label className="field" key={id}>{id.replaceAll('_', ' ')}<select value={id in scores ? String(scores[id]) : ''} onChange={e => { if (!e.target.value) setScores(s => { const copy = { ...s }; delete copy[id]; return copy }); else setScores(s => ({ ...s, [id]: e.target.value === 'true' })) }}><option value="">Chưa chấm</option><option value="true">Dùng được</option><option value="false">Chưa đạt</option></select></label>)}</div></div>)}</div>
      <div className="planner-fields"><label className="field">Sai actor<input type="number" min="0" value={wrongActors} onChange={e => setWrongActors(e.target.value)} /></label><label className="field">Ghi đè nội dung khóa<input type="number" min="0" value={overwrites} onChange={e => setOverwrites(e.target.value)} /></label><label className="field">Quyết định<select value={decision} onChange={e => setDecision(e.target.value)}><option value="REJECT">Chưa đạt</option><option value="APPROVE">Duyệt hồ sơ</option></select></label></div>
      <button disabled={busy || Object.keys(scores).length !== 30 || !wrongActors || !overwrites} onClick={() => work(() => record('HUMAN_REVIEW', { decision, actions: Object.fromEntries(catalog!.actions.map(id => [id, scores[id]])), cameras: Object.fromEntries(catalog!.cameras.map(id => [id, scores[id]])), wrong_actor_count: Number(wrongActors), manual_overwrite_count: Number(overwrites), notes }), 'Đã lưu review đúng record version. Không tự duyệt C1–C6 hoặc mở pilot dài.')}>Ghi review phiên bản này</button>
    </section></>}
    <section className="panel"><div className="panel-heading"><div><h3>Sao lưu toàn bộ Studio / phục hồi bản sao</h3><p>DB + manifests + tất cả media/cache trong project roots + project UE đã cấu hình. Phục hồi sang thư mục mới; không tự mở bản phục hồi để chạy production.</p></div></div>
      {(['backup_parent', 'restore_parent'] as const).map(field => <div className="setting-file" key={field}><label className="field grow">{field === 'backup_parent' ? 'Thư mục chứa backup' : 'Thư mục chứa bản phục hồi'}<input value={settings[field]} onChange={e => setSettings(s => ({ ...s, [field]: e.target.value }))} /></label><button disabled={busy} onClick={() => work(() => browseFolder(field), 'Đã chọn thư mục.')}>Duyệt…</button></div>)}
      <label className="acceptance-confirm"><input type="checkbox" checked={closed} onChange={e => setClosed(e.target.checked)} />Tôi đã đóng UE và các ứng dụng đang sửa media trong bản sao kiểm thử.</label>
      <div className="actions"><button disabled={busy || !settings.backup_parent || !settings.restore_parent} onClick={() => work(() => api('/settings/recovery', { expected_revision: settings.revision, backup_parent: settings.backup_parent, restore_parent: settings.restore_parent }), 'Đã lưu cấu hình sao lưu qua restart.')}>Lưu thư mục</button><button disabled={busy || !closed || !settings.revision} onClick={() => work(() => api('/recovery/jobs', { kind: 'BACKUP', ue_closed: closed }), 'Đã xếp sao lưu nền; job có thể bị chặn nếu còn công việc chưa đối soát.')}>Sao lưu mới</button></div>
      <div className="table-wrap"><table><thead><tr><th>Công việc</th><th>Trạng thái</th><th>Kết quả / lỗi</th><th></th></tr></thead><tbody>{recovery.map(j => <tr key={j.id}><td>{j.kind} · {j.id.slice(0, 10)}</td><td>{j.state}</td><td className="acceptance-result">{j.error ?? JSON.stringify(j.result)}</td><td>{j.kind === 'BACKUP' && j.state === 'COMPLETED' && <button disabled={busy || !closed} onClick={() => work(() => api('/recovery/jobs', { kind: 'RESTORE', backup_id: j.id, ue_closed: closed }), 'Đã xếp phục hồi vào thư mục mới.')}>Phục hồi bản sao</button>}</td></tr>)}</tbody></table></div>
      <p className="help">Kho khóa Windows không nằm trong backup. Không có project UE → LOCAL_ONLY_NO_UE. Copy hỏng giữ partial; restore chưa xác nhận UE/Resolve links hoặc rerender.</p>
    </section>
    <section className="panel"><h3>Hủy công việc local</h3><p className="help">Đang chạy → yêu cầu hủy; lease giữ tới khi worker dừng. Chưa có cancel UE/provider production.</p><div className="actions">{jobs.filter(j => ['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'].includes(j.state)).map(j => <button key={j.id} disabled={busy || j.state === 'CANCEL_REQUESTED'} onClick={() => work(() => api(`/jobs/${j.id}/cancel`, {}), 'Đã ghi yêu cầu hủy; không coi side effects đã được undo.')}>{j.id.slice(0, 10)} · {j.state} · Hủy</button>)}</div></section>
  </div>
}
