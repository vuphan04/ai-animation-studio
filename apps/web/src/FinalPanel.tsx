import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import { errorLabel, stateLabel } from './uiText'
import { t } from './i18n'

type Budget = { allowed: boolean; reason: string | null; near_cap?: boolean }
type QcCheck = { key: string; label: string; ok: boolean; detail: string; warning_only?: boolean }
type FinalShot = {
  shot_id: string; scene_key: string; route: string; description: string; length_frames: number; seconds: number; picture_locked: boolean
  source: { kind: string; label: string; expires_in_hours?: number | null; expired?: boolean; expiring?: boolean } | null
  estimate: { micro: number; currency: string } | null; issues: string[]
  final: { id: string; version: number; provenance: string; frames: number; width: number; height: number; provider_task_id: string | null; created_at: string; current: boolean; file_ok: boolean } | null
  conform: { id: string; version: number; qc: { checks: QcCheck[]; passed: boolean; codec: string; pix_fmt: string; color_space: string }; created_at: string; file_ok: boolean; current: boolean } | null
  job: { id: string; state: string; stage: string; error: string | null; provider_task_id: string | null } | null
  c8: { approved: boolean; stale: boolean }; c8_hash: string | null; checks: string[]; has_rough_segment: boolean; needs_final: boolean
}
type UeJob = { id: string; state: string; stage: string; error: string | null; shots: string[]; failed: { shot_id: string; error: string }[] }
type FinalState = {
  ready: boolean; blockers: string[]; shots: FinalShot[]; jobs: UeJob[]; ffmpeg_configured: boolean
  summary: { shots: number; locked: number; with_final: number; conformed: number; approved: number; ue_waiting?: number }
  gate3: { shots: number; totals: { currency: string; micro: number; budget: Budget }[]; allowed: boolean }
  standards?: { fps: { num: number; den: number }; width: number; height: number; color_space: string }; master_label?: string; unreal_configured?: boolean; color_supported?: boolean
  notes?: { final: string; watermark: string; retry: string }; import_note?: string | null
}

const checkLabels: Record<string, string> = { match: t('Khớp rough cut đã khóa: bố cục, nhân vật, chuyển động'), artifacts: t('Không lỗi hình: nhấp nháy, biến dạng, frame hỏng'), watermark: t('Không có watermark') }
const routeShort: Record<string, string> = { UE: 'UE', AI: 'AI', HYBRID: 'Hybrid' }
const provenanceLabel: Record<string, string> = { UE_MRQ: 'Movie Render Queue', AI_FINAL: t('nhà cung cấp tạo từ draft'), OPERATOR_FILE: t('file nhập tay') }
const stageLabel: Record<string, string> = { PREPARE: t('Unreal kiểm tra sequence và lập hàng đợi render'), RENDER: t('Unreal đang render frame final'), ENCODE: t('FFmpeg ghép frame và chuẩn hóa') }
const activeStates = ['QUEUED', 'RUNNING', 'PENDING']
const money = (micro: number, currency: string) => `${(micro / 1e6).toFixed(2)} ${currency}`
const fpsLabel = (fps: { num: number; den: number }) => fps.den === 1 ? `${fps.num}` : (fps.num / fps.den).toFixed(3)
const stamp = (value: string) => value.slice(0, 16).replace('T', ' ')

function shotStatus(shot: FinalShot): [string, string] {
  if (shot.c8.approved) return ['success', t('Đã duyệt final (C8)')]
  if (!shot.picture_locked) return ['neutral', t('Scene chưa khóa dựng')]
  if (shot.job && activeStates.includes(shot.job.state)) return ['neutral', t('Đang tạo final')]
  if (shot.final && !shot.final.current) return ['warning', t('Final thuộc bản dựng cũ')]
  if (shot.final && !shot.conform) return ['warning', t('Chưa chuẩn hóa được')]
  if (shot.conform && !shot.conform.current) return ['warning', t('Chuẩn series đã đổi · chuẩn hóa lại')]
  if (shot.conform && !shot.conform.qc.passed) return ['warning', t('Không qua kiểm tra tự động')]
  if (shot.c8.stale) return ['warning', t('Final đã đổi sau khi duyệt')]
  if (shot.conform) return ['neutral', t('Chờ bạn duyệt')]
  return ['neutral', t('Chưa có final')]
}

export default function FinalPanel({ episodeId, onChanged }: { episodeId: string; onChanged: () => void }) {
  const [state, setState] = useState<FinalState | null>(null)
  const [checks, setChecks] = useState<Record<string, Record<string, boolean>>>({})
  const [stopped, setStopped] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const players = useRef<Record<string, HTMLVideoElement | null>>({})

  const load = useCallback(async () => setState(await api<FinalState>(`/episodes/${episodeId}/finals`)), [episodeId])
  useEffect(() => { setChecks({}); setStopped(false); setError(''); setNotice(''); load().catch(e => setError(errorLabel(e))) }, [load])
  const working = Boolean(state && (state.jobs.some(j => activeStates.includes(j.state)) || state.shots.some(s => s.job && activeStates.includes(s.job.state))))
  useEffect(() => {
    if (!working) return
    const timer = setInterval(() => load().catch(e => setError(errorLabel(e))), 4000)
    return () => clearInterval(timer)
  }, [working, load])

  async function act(action: () => Promise<unknown>, message: string) {
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await action() as { import_note?: string | null } | undefined
      await load()
      if (result?.import_note) setError(result.import_note); else setNotice(message)
      onChanged()
    } catch (e) { setError(errorLabel(e)) } finally { setBusy(false) }
  }
  const playBoth = (shotId: string) => ['rough', 'final'].forEach(side => { const player = players.current[`${shotId}:${side}`]; if (player) { player.currentTime = 0; player.play().catch(() => undefined) } })

  const title = <div><h3>{t('10 Final → 11 Conform → C8 · Duyệt final kỹ thuật')}</h3><p>{t('Chỉ shot thuộc scene đã khóa dựng mới tạo final. Shot UE render bằng Movie Render Queue; shot AI và Hybrid tạo final từ draft đã chốt, cả lô qua cổng ngân sách ③. Mỗi final được chuẩn hóa về chuẩn của series, rồi bạn so với rough cut đã khóa và duyệt từng shot.')}</p></div>
  if (!state) return <section className="panel"><div className="panel-heading">{title}</div>{error && <div className="alert error" role="alert">{error}</div>}</section>

  const canEncode = state.ffmpeg_configured && state.color_supported !== false
  const ueActive = state.jobs.some(j => ['QUEUED', 'RUNNING'].includes(j.state))
  const stuck = state.jobs.find(j => j.state === 'RECONCILING')
  const ueWaiting = state.summary.ue_waiting ?? 0
  const std = state.standards

  return <section className="panel"><div className="panel-heading">{title}<span className={`status ${state.summary.shots > 0 && state.summary.approved === state.summary.shots ? 'success' : 'neutral'}`}>{state.summary.approved}/{state.summary.shots}{t(' shot đã duyệt final')}</span></div>
    {error && <div className="alert error" role="alert">{error}</div>}
    {notice && <div className="alert notice" role="status">{notice}</div>}
    {!state.ready && <div className="preflight blocked settings-result">{state.blockers.join(' ')}{t(' Hoàn tất ở tab Tập phim và Dựng shot trước khi tạo final.')}</div>}
    {state.ready && std && <>
      <div className="info-line"><span>{t('Chuẩn series (S1): ')}{std.width}×{std.height} · {fpsLabel(std.fps)} fps · {std.color_space} · master {state.master_label}{t(', không tiếng')}</span><strong>{state.ffmpeg_configured ? t('FFmpeg đã cấu hình') : t('Chưa cấu hình FFmpeg')}</strong></div>
      {!state.ffmpeg_configured && <div className="preflight blocked settings-result">{t('Cấu hình FFmpeg ở Cài đặt → Công cụ xử lý video. App không gửi yêu cầu final có phí khi chưa chuẩn hóa được kết quả.')}</div>}
      {state.color_supported === false && <div className="preflight blocked settings-result">{t('Chuẩn màu ')}{std.color_space}{t(' cần pipeline quản lý màu riêng; app chỉ chuẩn hóa được Rec.709 và sRGB. Đổi chuẩn màu ở S1 của Thư viện series hoặc xử lý màu ngoài app.')}</div>}
      <div className="info-line"><span>{state.summary.locked}/{state.summary.shots}{t(' shot thuộc scene đã khóa dựng · ')}{state.summary.with_final}{t(' shot có final · ')}{state.summary.conformed}{t(' shot đã chuẩn hóa và qua kiểm tra tự động')}</span><strong>{state.unreal_configured ? t('Unreal đã cấu hình') : t('Chưa cấu hình Unreal')}</strong></div>
      {state.summary.locked === 0 && <div className="preflight blocked settings-result">{t('Chưa có scene nào khóa dựng. Ghép rough cut và khóa dựng ở tab Dựng shot (C7) trước.')}</div>}

      <h4>{t('10 · Tạo final')}</h4>
      {state.gate3.totals.map(entry => <div key={entry.currency} className={`preflight ${entry.budget.allowed ? 'ready' : 'blocked'} settings-result`}><strong>{t('Cổng ngân sách ③ · ')}{entry.currency}:</strong>{t(' lô final của ')}{state.gate3.shots}{t(' shot AI/Hybrid dự kiến ')}{money(entry.micro, entry.currency)}. {entry.budget.allowed ? (entry.budget.near_cap ? t('Còn trong trần nhưng đã chạm ngưỡng cảnh báo của tháng.') : t('Còn trong trần chi phí tháng.')) : entry.budget.reason}</div>)}
      <div className="actions">
        <button className="button-primary" disabled={busy || !state.gate3.allowed || !canEncode} onClick={() => act(() => api(`/episodes/${episodeId}/finals/ai`, { shot_ids: null, client_request_id: crypto.randomUUID() }), t('Đã xếp lô final AI/Hybrid; khoản giữ nằm trong Sổ chi phí.'))}>{t('Gửi lô final AI/Hybrid · ')}{state.gate3.shots} shot</button>
        <button className="button-primary" disabled={busy || ueWaiting === 0 || ueActive || Boolean(stuck) || !state.unreal_configured || !canEncode} onClick={() => act(() => api(`/episodes/${episodeId}/finals/ue`, { shot_ids: null }), t('Đã xếp việc render final UE.'))}>{ueActive ? t('Đang render final UE…') : `Render final UE · ${ueWaiting} shot`}</button>
      </div>
      {state.gate3.shots === 0 && ueWaiting === 0 && state.summary.locked > 0 && <p className="help">{t('Không có shot nào đang chờ final. Muốn làm lại final của một shot, dùng nút trong thẻ shot bên dưới.')}</p>}
      {stuck && <div className="preflight blocked settings-result"><strong>{t('Lần render final trước bị gián đoạn.')}</strong>{t(' Đóng cửa sổ Unreal của lần render đó rồi xác nhận. ')}<label className="acceptance-confirm"><input type="checkbox" checked={stopped} onChange={e => setStopped(e.target.checked)} />{t('Tôi đã đóng Unreal của lần render cũ')}</label> <button disabled={busy || !stopped} onClick={() => act(async () => { await api(`/finals/jobs/${stuck.id}/release`, { worker_stopped: true }); setStopped(false) }, t('Đã xác nhận; có thể render lại.'))}>{t('Xác nhận và cho render lại')}</button></div>}
      {state.jobs.map(j => <div key={j.id} className="voice-job-list"><div><span className={`status ${j.state === 'COMPLETED' ? 'success' : ['BLOCKED', 'RECONCILING'].includes(j.state) ? 'warning' : 'neutral'}`}>{stateLabel(j.state)}</span>{t(' Render final UE · ')}{j.shots.length} shot{j.state === 'RUNNING' ? ` · ${stageLabel[j.stage] ?? j.stage}` : ''}{j.error && <small className="row-error"> {j.error}</small>}{j.failed.map(f => <small key={f.shot_id + f.error} className="row-error"> {f.shot_id}: {f.error}</small>)}</div></div>)}

      <h4>{t('11 · Chuẩn hóa và C8 · Duyệt từng shot')}</h4>
      {state.shots.map(shot => {
        const [tone, label] = shotStatus(shot); const marks = checks[shot.shot_id] ?? {}; const ai = shot.route !== 'UE'
        const active = Boolean(shot.job && activeStates.includes(shot.job.state)); const reviewable = Boolean(shot.c8_hash) && !shot.c8.approved
        return <details key={shot.shot_id} className="scene-card" open={shot.picture_locked && !shot.c8.approved}><summary><strong>{shot.shot_id} · {routeShort[shot.route]}</strong><span className="muted">{shot.length_frames} frame · {shot.seconds.toFixed(2)}{t(' giây')}{shot.estimate ? ` · final ${money(shot.estimate.micro, shot.estimate.currency)}` : ''}</span><span className={`status ${tone}`}>{label}</span></summary>
          <p className="help">{shot.description}</p>
          <p className="help"><strong>{t('Nguồn final:')}</strong> {shot.source?.label}{shot.source?.expiring && shot.needs_final ? t(' · draft còn {0} giờ để tạo final', Math.max(1, Math.round(shot.source.expires_in_hours ?? 0))) : ''}</p>
          {shot.issues.map(i => <p key={i} className="row-error">{i}</p>)}
          <div className="actions">
            {ai ? <button disabled={busy || !shot.picture_locked || shot.issues.length > 0 || active || shot.job?.state === 'RECONCILING' || !shot.estimate || !canEncode} onClick={() => act(() => api(`/episodes/${episodeId}/finals/ai`, { shot_ids: [shot.shot_id], client_request_id: crypto.randomUUID() }), t('Đã xếp yêu cầu final cho {0}.', shot.shot_id))}>{shot.final ? t('Tạo lại final shot này') : t('Tạo final shot này')}{shot.estimate ? t(' · giữ {0}', money(shot.estimate.micro, shot.estimate.currency)) : ''}</button>
              : <button disabled={busy || !shot.picture_locked || shot.issues.length > 0 || ueActive || Boolean(stuck) || !state.unreal_configured || !canEncode} onClick={() => act(() => api(`/episodes/${episodeId}/finals/ue`, { shot_ids: [shot.shot_id] }), t('Đã xếp việc render final cho {0}.', shot.shot_id))}>{shot.final ? t('Render lại final shot này') : t('Render final shot này')}</button>}
            <button disabled={busy || !shot.picture_locked || !canEncode} onClick={() => { setNotice(t('Đang mở cửa sổ Windows; kiểm tra thanh tác vụ nếu chưa thấy.')); act(() => api(`/episodes/${episodeId}/finals/shots/${shot.shot_id}/import/browse`, {}), t('Đã nhập final làm tay cho {0}.', shot.shot_id)) }}>{t('Nhập final làm tay…')}</button>
            {shot.final?.current && <button className="button-quiet" disabled={busy || !canEncode} onClick={() => act(() => api(`/episodes/${episodeId}/finals/shots/${shot.shot_id}/conform`, {}), t('Đã chuẩn hóa lại final của {0}.', shot.shot_id))}>{t('Chuẩn hóa lại')}</button>}
            {shot.job?.state === 'PENDING' && <button disabled={busy} onClick={() => act(() => api(`/finals/jobs/${shot.job!.id}/stop-waiting`, {}), t('Đã dừng chờ; khoản giữ còn trong Sổ chi phí.'))}>{t('Dừng chờ nhà cung cấp')}</button>}
          </div>
          {shot.job && shot.job.state !== 'COMPLETED' && <p className={['BLOCKED', 'RECONCILING'].includes(shot.job.state) ? 'row-error' : 'help'}>{stateLabel(shot.job.state)}{shot.job.provider_task_id ? ` · task ${shot.job.provider_task_id}` : ''}{shot.job.error ? ` · ${shot.job.error}` : ''}{shot.job.state === 'RECONCILING' ? t(' Kiểm tra tài khoản nhà cung cấp rồi đóng khoản giữ trong Sổ chi phí (Thư viện series → S1) trước khi gửi lại.') : ''}</p>}
          {shot.job?.state === 'COMPLETED' && shot.job.error && <p className="row-error">{shot.job.error}</p>}
          {shot.final && <p className="help"><strong>{t('Final bản ')}{shot.final.version}</strong> · {provenanceLabel[shot.final.provenance] ?? shot.final.provenance} · {shot.final.width}×{shot.final.height} · {shot.final.frames} frame · {stamp(shot.final.created_at)}{shot.final.provider_task_id ? ` · task ${shot.final.provider_task_id}` : ''}{shot.final.current ? '' : t(' · thuộc bản dựng cũ, không dùng')}{shot.final.file_ok ? '' : t(' · thiếu file trong thư mục dự án')}</p>}
          {shot.conform && <>
            <div className="final-compare">
              <figure><figcaption>{t('Rough cut đã khóa')}</figcaption>{shot.has_rough_segment ? <video ref={element => { players.current[`${shot.shot_id}:rough`] = element }} className="final-video" controls muted preload="none" src={`/api/episodes/${episodeId}/rough-cut/segments/${shot.shot_id}`} aria-label={t('Đoạn rough cut của {0}', shot.shot_id)} /> : <p className="help">{t('Chưa có đoạn rough cut của shot này trong dự án.')}</p>}</figure>
              <figure><figcaption>{t('Final đã chuẩn hóa · bản ')}{shot.conform.version} · {stamp(shot.conform.created_at)}</figcaption>{shot.conform.file_ok ? <video key={shot.conform.id} ref={element => { players.current[`${shot.shot_id}:final`] = element }} className="final-video" controls muted preload="none" src={`/api/finals/review/${shot.conform.id}`} aria-label={t('Final đã chuẩn hóa của {0}', shot.shot_id)} /> : <p className="row-error">{t('File chuẩn hóa thiếu trong thư mục dự án; bấm “Chuẩn hóa lại”.')}</p>}</figure>
            </div>
            <div className="actions"><button className="button-quiet" disabled={!shot.has_rough_segment || !shot.conform.file_ok} onClick={() => playBoth(shot.shot_id)}>{t('Phát cả hai từ đầu')}</button><span className="help">{t('Bản xem ở đây là bản nén để so sánh; master là ')}{state.master_label} ({shot.conform.qc.codec}, {shot.conform.qc.pix_fmt}).</span></div>
            <ul className="qc-list">{shot.conform.qc.checks.map(c => <li key={c.key} className={c.ok ? 'ok' : c.warning_only ? 'warn' : 'fail'}>{c.ok ? '✓' : c.warning_only ? '!' : '✕'} {c.label} · {c.detail}</li>)}</ul>
            {!shot.conform.qc.passed && <p className="row-error">{t('Final này không qua kiểm tra tự động nên chưa duyệt được. Tạo lại final riêng shot này; khóa dựng không bị mở.')}</p>}
          </>}
          {shot.c8.stale && <p className="row-error">{t('Final hoặc bản dựng đã đổi sau lần duyệt trước; xem lại và duyệt lại.')}</p>}
          {reviewable && <>
            <div className="actions">{shot.checks.map(key => <label key={key} className="acceptance-confirm"><input type="checkbox" checked={Boolean(marks[key])} onChange={e => setChecks({ ...checks, [shot.shot_id]: { ...marks, [key]: e.target.checked } })} />{checkLabels[key] ?? key}</label>)}</div>
            <div className="actions"><button className="button-primary" disabled={busy || shot.checks.some(key => !marks[key])} onClick={() => act(async () => { await api(`/episodes/${episodeId}/finals/shots/${shot.shot_id}/c8`, { expected_hash: shot.c8_hash, checks: Object.fromEntries(shot.checks.map(key => [key, true])) }); setChecks(Object.fromEntries(Object.entries(checks).filter(([k]) => k !== shot.shot_id))) }, t('Đã duyệt final của {0} (C8).', shot.shot_id))}>{t('Duyệt final (C8)')}</button></div>
          </>}
          {shot.c8.approved && <div className="actions"><button disabled={busy} onClick={() => act(() => api(`/episodes/${episodeId}/finals/shots/${shot.shot_id}/c8/revoke`, {}), t('Đã bỏ duyệt final của {0}.', shot.shot_id))}>{t('Bỏ duyệt final')}</button></div>}
        </details>
      })}
      {state.notes && <p className="help">{state.notes.final} {state.notes.watermark} {state.notes.retry}{t(' Chi phí ghi theo giá final bạn nhập ở C3 nhân số giây tạo; không phải hóa đơn của nhà cung cấp.')}</p>}
    </>}
  </section>
}
