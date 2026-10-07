import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { errorLabel, stateLabel } from './uiText'
import { t } from './i18n'

type Take = { id: string; version: number; review: string; model: string; provider_task_id: string | null; requested_seconds: number; measured_seconds: number | null; prompt: string; checks: Record<string, boolean>; current: boolean; file_ok: boolean; expires_at: string | null; expires_in_hours: number | null; expired: boolean; expiring: boolean; has_final: boolean; locked: boolean }
type Reference = { key: string; entity: string; role: string; selected: boolean; file_ok: boolean }
type AiShot = { shot_id: string; route: string; model: string; model_label: string; description: string; seconds: number; generate_seconds: number; resolution: string; prompt: string; prompt_override: string | null; references: Reference[]; reference_limit: number; first_frame_only: boolean; control: { id: string; kind: string; version: number; file_ok: boolean } | null; ue_source: { locked: boolean; notes: { keep: string; ai_change: string } | null } | null; revision: number; estimated_micro: number; currency: string; issues: string[]; takes: Take[]; job: { id: string; state: string; error: string | null; provider_task_id: string | null } | null; c6: { locked: boolean; take_id: string | null; stale: boolean; expired: boolean; expiring: boolean } }
type AiState = { ready: boolean; blockers: string[]; models: { model: string; label: string; platform: string; configured: boolean; verified_at: string | null }[]; shots: AiShot[]; summary: { shots: number; with_draft: number; approved: number; locked: number; expiring: number; working: number }; audio_note?: string; final_note?: string }
type Draft = { prompt: string; references: string[] }

const checkLabels: [string, string][] = [['characters', t('Nhân vật đúng')], ['action', t('Hành động đúng')], ['camera', t('Camera đúng')], ['style', t('Style đúng')]]
const roleLabel: Record<string, string> = { turnaround_front: t('chính diện'), turnaround_three_quarter: t('góc 3/4'), turnaround_side: t('nghiêng'), turnaround_back: t('sau lưng'), concept: 'concept', frame_01: 'frame 1', frame_02: 'frame 2', frame_03: 'frame 3', frame_04: 'frame 4' }
const money = (micro: number, currency: string) => `${(micro / 1e6).toFixed(2)} ${currency}`
const expiry = (take: Take) => take.has_final ? t(' · đã có final tạo từ draft này') : take.expires_in_hours === null ? '' : take.expired ? t(' · đã quá hạn dùng để tạo final') : t(' · còn {0} để tạo final', take.expires_in_hours >= 48 ? t('{0} ngày', Math.floor(take.expires_in_hours / 24)) : t('{0} giờ', Math.max(1, Math.round(take.expires_in_hours))))

export default function AiBranchPanel({ episodeId, refreshKey, onChanged }: { episodeId: string; refreshKey: number; onChanged: () => void }) {
  const [state, setState] = useState<AiState | null>(null)
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const [checks, setChecks] = useState<Record<string, Record<string, boolean>>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(async () => setState(await api<AiState>(`/episodes/${episodeId}/ai-video`)), [episodeId])
  useEffect(() => { setDrafts({}); setChecks({}); setError(''); setNotice(''); load().catch(e => setError(errorLabel(e))) }, [load, refreshKey])
  const working = (state?.summary.working ?? 0) > 0
  useEffect(() => {
    if (!working) return
    const timer = setInterval(() => load().catch(e => setError(errorLabel(e))), 4000)
    return () => clearInterval(timer)
  }, [working, load])

  async function act(action: () => Promise<unknown>, message: string) {
    setBusy(true); setError(''); setNotice('')
    try { await action(); await load(); setNotice(message); onChanged() } catch (e) { setError(errorLabel(e)) } finally { setBusy(false) }
  }

  const title = <div><h3>{t('Nhánh AI và draft Hybrid · 07, 08, C5, 09/C6')}</h3><p>{t('Chuẩn bị prompt theo Style bible và ảnh tham chiếu, tạo draft qua cổng ngân sách ②, bạn xem và duyệt draft, rồi chốt draft cho rough cut. Final chỉ tạo sau khi khóa dựng.')}</p></div>
  if (!state) return <section className="panel"><div className="panel-heading">{title}</div>{error && <div className="alert error" role="alert">{error}</div>}</section>
  const draft = (shot: AiShot): Draft => drafts[shot.shot_id] ?? { prompt: shot.prompt_override ?? '', references: shot.references.filter(r => r.selected).map(r => r.key) }
  const setDraft = (shot: AiShot, change: Partial<Draft>) => setDrafts({ ...drafts, [shot.shot_id]: { ...draft(shot), ...change } })

  return <section className="panel"><div className="panel-heading">{title}<span className={`status ${state.summary.shots > 0 && state.summary.locked === state.summary.shots ? 'success' : 'neutral'}`}>{state.summary.locked}/{state.summary.shots}{t(' shot đã chốt draft')}</span></div>
    {error && <div className="alert error" role="alert">{error}</div>}
    {notice && <div className="alert notice" role="status">{notice}</div>}
    {!state.ready && <div className="preflight blocked settings-result">{state.blockers.join(' ')}{t(' Hoàn tất ở tab Tập phim trước khi tạo draft.')}</div>}
    {state.ready && state.shots.length === 0 && <p className="help">{t('Tập này không có shot AI hoặc Hybrid.')}</p>}
    {state.ready && state.shots.length > 0 && <>
      <div className="info-line"><span>{state.models.map(m => `${m.label}: ${m.configured ? (m.verified_at ? t('đã nhận draft') : t('đã cấu hình, chưa có draft nào')) : t('chưa cấu hình')}`).join(' · ')}</span><strong>{state.summary.with_draft}/{state.summary.shots}{t(' shot có draft · ')}{state.summary.approved}{t(' đã duyệt')}</strong></div>
      {state.summary.expiring > 0 && <div className="preflight blocked settings-result"><strong>{state.summary.expiring}{t(' draft đã chốt sắp hết hoặc đã hết hạn 7 ngày.')}</strong>{t(' Draft Seedance quá hạn không dùng để tạo final được; tạo draft mới trước khi sang bước final.')}</div>}
      {state.shots.map(shot => { const d = draft(shot); const dirty = Boolean(drafts[shot.shot_id]); const active = shot.job && ['QUEUED', 'RUNNING', 'PENDING'].includes(shot.job.state); return <details key={shot.shot_id} className="scene-card" open={!shot.c6.locked}><summary><strong>{shot.shot_id} · {shot.route === 'HYBRID' ? 'Hybrid' : 'AI'} · {shot.model_label}</strong><span className="muted">{shot.seconds.toFixed(2)}{t(' giây theo C4 · tạo ')}{shot.generate_seconds}{t(' giây ở ')}{shot.resolution} · {money(shot.estimated_micro, shot.currency)}{t(' mỗi draft')}</span><span className={`status ${shot.c6.locked ? 'success' : shot.issues.length || shot.c6.stale || shot.c6.expired ? 'warning' : 'neutral'}`}>{shot.c6.locked ? t('Đã chốt draft (C6)') : shot.c6.expired ? t('Draft đã chốt quá hạn') : shot.c6.stale ? t('Đầu vào đã đổi sau khi chốt') : shot.takes.some(entry => entry.current && entry.review === 'APPROVED') ? t('Draft đã duyệt · chưa chốt') : shot.takes.some(entry => entry.current) ? t('Chờ bạn xem draft') : t('Chưa có draft')}</span></summary>
        <p className="help">{shot.description}</p>
        {shot.route === 'HYBRID' && <div className="route-shot">
          <p className="help"><strong>{t('Nguồn UE:')}</strong> {shot.ue_source?.locked ? t('đã chốt ở C5') : t('chưa chốt ở C5 (nhánh UE phía trên)')}{shot.ue_source?.notes ? t(' · giữ nguyên: {0} · AI sửa: {1}', shot.ue_source.notes.keep, shot.ue_source.notes.ai_change) : ''}</p>
          <div className="actions"><button disabled={busy} onClick={() => { setNotice(t('Đang mở cửa sổ Windows; kiểm tra thanh tác vụ nếu chưa thấy.')); act(() => api(`/episodes/${episodeId}/ai-video/shots/${shot.shot_id}/control/browse`, {}), t('Đã đăng ký nguồn UE của shot Hybrid.')) }}>{t('Chọn nguồn UE bằng tay…')}</button>{shot.control && (shot.control.kind === 'FIRST_FRAME' ? <img className="control-preview" src={`/api/ai-video/controls/${shot.control.id}/file`} alt={t('Frame đầu của {0}', shot.shot_id)} /> : <video className="control-preview" controls preload="none" src={`/api/ai-video/controls/${shot.control.id}/file`} aria-label={t('Video nguồn UE của {0}', shot.shot_id)} />)}{shot.control && <span className="help">{shot.control.kind === 'FIRST_FRAME' ? t('Frame đầu') : t('Video nguồn')}{t(' · bản ')}{shot.control.version}</span>}</div>
        </div>}
        <h5>{t('07 · Đầu vào')}</h5>
        <p className="help">{t('Prompt sẽ gửi: ')}<code>{shot.prompt || t('chưa có')}</code></p>
        <label className="field">{t('Prompt riêng cho shot (tiếng Anh; để trống = dùng prompt của breakdown; app tự nối cụm từ style)')}<textarea rows={2} maxLength={1500} value={d.prompt} onChange={e => setDraft(shot, { prompt: e.target.value })} /></label>
        {shot.references.length > 0 && <div className="actions">{shot.references.map(ref => <label key={ref.key} className="acceptance-confirm"><input type="checkbox" disabled={shot.first_frame_only || shot.reference_limit === 0 || (!d.references.includes(ref.key) && d.references.length >= shot.reference_limit)} checked={d.references.includes(ref.key)} onChange={e => setDraft(shot, { references: e.target.checked ? [...d.references, ref.key] : d.references.filter(k => k !== ref.key) })} />{ref.entity} · {roleLabel[ref.role] ?? ref.role}{!ref.file_ok ? t(' (thiếu file)') : ''}</label>)}</div>}
        <p className="help">{shot.first_frame_only ? t('Shot này gửi frame đầu của nguồn UE; ảnh tham chiếu không gửi kèm vì hai chế độ không trộn được.') : shot.reference_limit === 0 ? t('{0} không nhận ảnh tham chiếu Style bible trong yêu cầu draft; nhân vật dựa vào mô tả trong prompt.', shot.model_label) : t('Gửi tối đa {0} ảnh tham chiếu đã duyệt của Style bible.', shot.reference_limit)}</p>
        <div className="actions"><button disabled={busy || !dirty} onClick={() => act(async () => { await api(`/episodes/${episodeId}/ai-video/shots/${shot.shot_id}/input`, { expected_revision: shot.revision, prompt_override: d.prompt.trim() || null, reference_keys: d.references }); setDrafts(Object.fromEntries(Object.entries(drafts).filter(([k]) => k !== shot.shot_id))) }, t('Đã lưu đầu vào của {0}; draft cũ không còn khớp nếu prompt hoặc tham chiếu đổi.', shot.shot_id))}>{t('Lưu đầu vào')}</button></div>
        {shot.issues.map(i => <p key={i} className="row-error">{i}</p>)}
        <h5>08 · Draft</h5>
        <div className="actions"><button className="button-primary" disabled={busy || dirty || Boolean(active) || shot.issues.length > 0 || shot.job?.state === 'RECONCILING'} onClick={() => act(() => api(`/episodes/${episodeId}/ai-video/shots/${shot.shot_id}/draft`, { client_request_id: crypto.randomUUID() }), t('Đã xếp yêu cầu draft cho {0}; giữ {1} trong Sổ chi phí.', shot.shot_id, money(shot.estimated_micro, shot.currency)))}>{shot.takes.length ? t('Tạo draft mới') : t('Tạo draft')}{t(' · giữ ')}{money(shot.estimated_micro, shot.currency)}</button>{shot.job?.state === 'PENDING' && <button disabled={busy} onClick={() => act(() => api(`/ai-video/jobs/${shot.job!.id}/stop-waiting`, {}), t('Đã dừng chờ; khoản giữ còn trong Sổ chi phí.'))}>{t('Dừng chờ nhà cung cấp')}</button>}</div>
        {shot.job && shot.job.state !== 'COMPLETED' && <p className={['BLOCKED', 'RECONCILING'].includes(shot.job.state) ? 'row-error' : 'help'}>{stateLabel(shot.job.state)}{shot.job.provider_task_id ? ` · task ${shot.job.provider_task_id}` : ''}{shot.job.error ? ` · ${shot.job.error}` : ''}{shot.job.state === 'RECONCILING' ? t(' Kiểm tra tài khoản nhà cung cấp rồi đóng khoản giữ trong Sổ chi phí (Thư viện series → S1) trước khi gửi lại.') : ''}</p>}
        {shot.takes.map(take => { const marks = checks[take.id] ?? take.checks; return <div key={take.id} className="route-shot">
          <div className="route-shot-head"><strong>Draft {take.version}</strong><span className="muted">{take.measured_seconds !== null ? t('{0} giây (đo)', take.measured_seconds) : t('{0} giây (theo yêu cầu, chưa đo)', take.requested_seconds)} · task {take.provider_task_id ?? '—'}{expiry(take)}{!take.current ? t(' · prompt, tham chiếu hoặc timing đã đổi') : ''}</span><span className={`status ${take.locked && take.current ? 'success' : take.review === 'REJECTED' || (take.expired && !take.has_final) ? 'warning' : 'neutral'}`}>{take.locked ? t('Đang chốt') : stateLabel(take.review)}</span></div>
          {take.file_ok ? <video className="draft-video" controls preload="none" src={`/api/ai-video/takes/${take.id}/file`} aria-label={t('Xem draft {0} của {1}', take.version, shot.shot_id)} /> : <p className="row-error">{t('File draft thiếu trong thư mục dự án.')}</p>}
          {take.review === 'NEEDS_REVIEW' && take.current && <div className="actions">{checkLabels.map(([key, label]) => <label key={key} className="acceptance-confirm"><input type="checkbox" checked={Boolean(marks[key])} onChange={e => setChecks({ ...checks, [take.id]: { ...marks, [key]: e.target.checked } })} />{label}</label>)}</div>}
          <div className="actions">{take.review === 'NEEDS_REVIEW' && take.current && <button className="button-primary" disabled={busy || !take.file_ok || checkLabels.some(([key]) => !marks[key])} onClick={() => act(() => api(`/ai-video/takes/${take.id}/review`, { decision: 'APPROVED', checks: Object.fromEntries(checkLabels.map(([key]) => [key, true])) }), t('Đã duyệt draft {0} của {1}.', take.version, shot.shot_id))}>{shot.route === 'HYBRID' ? t('Duyệt draft') : t('Duyệt draft (C5)')}</button>}
            {take.review !== 'REJECTED' && <button className="button-quiet" disabled={busy} onClick={() => act(() => api(`/ai-video/takes/${take.id}/review`, { decision: 'REJECTED', checks: {} }), t('Đã từ chối draft {0}.', take.version))}>{t('Từ chối')}</button>}
            {take.review === 'APPROVED' && take.current && !take.locked && <button className="button-primary" disabled={busy || (take.expired && !take.has_final) || !take.file_ok} onClick={() => act(() => api(`/episodes/${episodeId}/ai-video/shots/${shot.shot_id}/c6`, { take_id: take.id }), t('Đã chốt draft {0} cho rough cut (C6).', take.version))}>{t('Chốt draft này cho rough cut (C6)')}</button>}
            {take.locked && <button disabled={busy} onClick={() => act(() => api(`/episodes/${episodeId}/ai-video/shots/${shot.shot_id}/c6/unlock`, {}), t('Đã bỏ chốt draft.'))}>{t('Bỏ chốt')}</button>}</div>
        </div> })}
        <p className="help">{t('Đổi model hoặc đổi phương thức của shot: mở khóa shot ở C3 trong tab Tập phim, đổi rồi chốt lại C3.')}</p>
      </details> })}
      <p className="help">{state.audio_note} {state.final_note}{t(' Chi phí ghi theo giá bạn nhập ở C3 nhân số giây tạo; không phải hóa đơn của nhà cung cấp.')}</p>
    </>}
  </section>
}
