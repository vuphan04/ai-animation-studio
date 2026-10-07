import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import { errorLabel, stateLabel } from './uiText'
import { t } from './i18n'

type QcCheck = { key: string; label: string; ok: boolean; detail: string; warning_only?: boolean }
type Package = { id: string; version: number; created_at: string; folder: string; frames: number; seconds: number; qc: { checks: QcCheck[]; passed: boolean; warnings: number; target_lufs: number }; files: { path: string; bytes: number }[]; file_ok: boolean; current: boolean }
type Export = { id: string; path: string; bytes: number; sha256: string; info: { frames: number; width: number; height: number; fps_num: number; fps_den: number; audio_streams: number; notes: string[] }; created_at: string; file_ok: boolean }
type DeliveryState = {
  ready: boolean; blockers: string[]; package: Package | null; export: Export | null; c10: { sequence: number; created_at: string; current: boolean } | null
  job: { id: string; state: string; stage: string; error: string | null } | null; ffmpeg_configured: boolean; loudness_targets: { lufs: number; label: string }[]; master_label: string
  summary: { shots: number; approved: number; lines: number; missing_en: number }; c10_hash?: string | null; checks?: string[]; approve_blockers?: string[]; allowed?: boolean
  seconds?: number; total_frames?: number; notes?: { resolve: string; timeline: string; upscale: string }
}

const stageLabel: Record<string, string> = { PREPARE: t('chép media, ghi timeline và phụ đề'), PREVIEW: t('xuất MP4 preview'), QC: t('chạy QC tự động') }
const checkLabels: Record<string, string> = { relink: t('Đã import timeline và relink đủ media'), look: t('Đã áp LUT chung và grain cho mọi shot'), mix: t('Đã mix âm thanh và đạt loudness mục tiêu'), watch: t('Đã xem bản xuất từ đầu đến cuối'), qc_warnings: t('Đã xem các cảnh báo QC tự động ở trên') }
const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${(seconds % 60).toFixed(1).padStart(4, '0')}`
const stamp = (value: string) => value.slice(0, 16).replace('T', ' ')
const size = (bytes: number) => bytes >= 1e9 ? `${(bytes / 1e9).toFixed(2)} GB` : bytes >= 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1e3))} KB`

export default function DeliveryPanel({ episodeId, refreshKey }: { episodeId: string; refreshKey: number }) {
  const [state, setState] = useState<DeliveryState | null>(null)
  const [target, setTarget] = useState(-14)
  const [checks, setChecks] = useState<Record<string, boolean>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const targetLoaded = useRef(false)

  const load = useCallback(async () => { const next = await api<DeliveryState>(`/episodes/${episodeId}/delivery`); setState(next); return next }, [episodeId])
  // The target of the last package is the starting choice once; later reloads keep what the operator picked.
  useEffect(() => { setChecks({}); setError(''); setNotice(''); load().then(next => { if (next.package && !targetLoaded.current) { targetLoaded.current = true; setTarget(next.package.qc.target_lufs) } }).catch(e => setError(errorLabel(e))) }, [load, refreshKey])
  const working = Boolean(state?.job && ['QUEUED', 'RUNNING'].includes(state.job.state))
  useEffect(() => {
    if (!working) return
    const timer = setInterval(() => load().catch(e => setError(errorLabel(e))), 4000)
    return () => clearInterval(timer)
  }, [working, load])

  async function act(action: () => Promise<unknown>, message: string) {
    setBusy(true); setError(''); setNotice('')
    try { await action(); await load(); setNotice(message) } catch (e) { setError(errorLabel(e)) } finally { setBusy(false) }
  }

  const title = <div><h3>{t('13 · Bản dựng, gói timeline và QC → C10 · Import DaVinci Resolve và xuất phim')}</h3><p>{t('App ghép final của mọi shot theo thứ tự và timing đã chốt, đặt mỗi stem vào một track riêng, rồi ghi một thư mục gói: timeline, media, phụ đề SRT tiếng Việt và tiếng Anh, MP4 preview và báo cáo QC. Bạn import gói vào DaVinci Resolve để grade, mix và xuất phim.')}</p></div>
  if (!state) return <section className="panel"><div className="panel-heading">{title}</div>{error && <div className="alert error" role="alert">{error}</div>}</section>
  const pack = state.package; const sent = state.export; const wanted = state.checks ?? []

  return <section className="panel"><div className="panel-heading">{title}<span className={`status ${state.c10?.current ? 'success' : state.c10 || (pack && !pack.current) ? 'warning' : 'neutral'}`}>{state.c10?.current ? t('Đã xuất phim (C10) · lần {0}', state.c10.sequence) : state.c10 ? t('Đã đổi sau khi xác nhận') : pack?.current ? t('Gói bản {0} · chờ xuất phim', pack.version) : pack ? t('Gói đã cũ') : t('Chưa có gói')}</span></div>
    {error && <div className="alert error" role="alert">{error}</div>}
    {notice && <div className="alert notice" role="status">{notice}</div>}
    {!state.ready && <div className="preflight blocked settings-result"><strong>{t('Chưa tạo được gói:')}</strong> {state.blockers.join(' ')}</div>}
    <div className="info-line"><span>{state.summary.approved}/{state.summary.shots}{t(' shot đã duyệt final · ')}{state.summary.lines}{t(' câu thoại có phụ đề')}{state.summary.missing_en > 0 ? t(' · thiếu bản dịch tiếng Anh của {0} câu (bổ sung ở C1)', state.summary.missing_en) : ''}{state.seconds ? t(' · dài {0}', clock(state.seconds)) : ''}</span><strong>{state.ffmpeg_configured ? t('FFmpeg đã cấu hình') : t('Chưa cấu hình FFmpeg')}</strong></div>

    <h4>{t('13 · Tạo gói bàn giao')}</h4>
    <div className="form-row"><label className="field">{t('Loudness mục tiêu của phim')}<select value={target} onChange={e => setTarget(Number(e.target.value))}>{state.loudness_targets.map(entry => <option key={entry.lufs} value={entry.lufs}>{entry.label}</option>)}</select></label>
      <button className={pack?.current ? '' : 'button-primary'} disabled={busy || working || !state.ready || !state.ffmpeg_configured} onClick={() => act(() => api(`/episodes/${episodeId}/delivery/build`, { loudness_lufs: target }), t('Đã xếp việc tạo gói bàn giao.'))}>{working ? t('Đang tạo gói…') : pack ? t('Tạo lại gói bàn giao') : t('Tạo gói bàn giao')}</button></div>
    {state.job && state.job.state !== 'COMPLETED' && <p className={state.job.state === 'BLOCKED' ? 'row-error' : 'help'}>{stateLabel(state.job.state)}{state.job.state === 'RUNNING' ? t(' · đang {0}', stageLabel[state.job.stage] ?? state.job.stage) : ''}{state.job.error ? ` · ${state.job.error}` : ''}</p>}
    {pack && <>
      <p className="help"><strong>{t('Gói bản ')}{pack.version}</strong>{t(' · tạo lúc ')}{stamp(pack.created_at)} · {pack.frames} frame ({clock(pack.seconds)}) · master {state.master_label}{pack.current ? '' : t(' · final, âm thanh hoặc phụ đề đã đổi sau khi tạo gói; tạo lại trước khi bàn giao')}{pack.file_ok ? '' : t(' · thiếu file trong thư mục gói')}</p>
      <div className="actions"><code>{pack.folder}</code><button disabled={busy || !pack.file_ok} onClick={() => act(() => api(`/episodes/${episodeId}/delivery/open-folder`, {}), t('Đã mở thư mục gói trong Explorer.'))}>{t('Mở thư mục gói')}</button></div>
      {pack.file_ok && <video key={pack.id} className="rough-video" controls preload="metadata" src={`/api/delivery/${pack.id}/preview`} aria-label={t('MP4 preview của gói bàn giao')} />}
      <ul className="qc-list">{pack.qc.checks.map(c => <li key={c.key} className={c.ok ? 'ok' : c.warning_only ? 'warn' : 'fail'}>{c.ok ? '✓' : c.warning_only ? '!' : '✕'} {c.label} · {c.detail}</li>)}</ul>
      {!pack.qc.passed && <p className="row-error">{t('Gói không qua QC tự động nên chưa bàn giao được. Sửa nguyên nhân ở bước trước (final, âm thanh) rồi tạo lại gói.')}</p>}
      {pack.qc.passed && pack.qc.warnings > 0 && <p className="help">{pack.qc.warnings}{t(' cảnh báo: frame đen và đứng hình có thể là chủ ý; loudness chỉnh khi mix trong Resolve; phụ đề thiếu thì bổ sung bản dịch ở C1.')}</p>}
      <details className="scene-card"><summary><strong>{t('File trong gói')}</strong><span className="muted">{pack.files.length}{t(' file · timeline FCPXML, EDL dự phòng, media, phụ đề, preview, QC')}</span></summary><div className="table-wrap"><table><tbody>{pack.files.map(f => <tr key={f.path}><td>{f.path}</td><td>{size(f.bytes)}</td></tr>)}</tbody></table></div></details>
    </>}

    <h4>{t('C10 · Import vào DaVinci Resolve, grade, mix và xuất phim')}</h4>
    <ol className="help"><li>{t('Trong Resolve: File → Import → Timeline, chọn file ')}<code>.fcpxml</code>{t(' trong thư mục gói; nếu media báo offline thì relink tới thư mục ')}<code>media</code>{t(' của gói.')}</li><li>{t('Áp LUT chung và grain cho mọi shot để thống nhất look; mix âm thanh trên các track stem.')}</li><li>{t('Import phụ đề từ thư mục ')}<code>subtitles</code>{t(', xuất phim, rồi đăng ký file đã xuất ở đây.')}</li></ol>
    {state.notes && <p className="help">{state.notes.resolve} {state.notes.timeline} {state.notes.upscale}</p>}
    <div className="actions"><button disabled={busy || !pack?.current || !pack.qc.passed} onClick={() => { setNotice(t('Đang mở cửa sổ Windows; kiểm tra thanh tác vụ nếu chưa thấy.')); act(() => api(`/episodes/${episodeId}/delivery/export/browse`, {}), t('Đã đăng ký file phim đã xuất.')) }}>{sent ? t('Chọn file phim khác…') : t('Chọn file phim đã xuất…')}</button></div>
    {sent && <>
      <p className="help"><strong>{t('Phim đã đăng ký:')}</strong> <code>{sent.path}</code> · {size(sent.bytes)} · {sent.info.width}×{sent.info.height} · {sent.info.frames} frame · {sent.info.audio_streams > 0 ? t('có âm thanh') : t('không có âm thanh')} · {stamp(sent.created_at)}{sent.file_ok ? '' : t(' · file không còn ở vị trí này hoặc đã đổi')}</p>
      {sent.info.notes.map(n => <p key={n} className="row-error">{n}</p>)}
    </>}
    {(state.approve_blockers?.length ?? 0) > 0 && pack && !state.c10?.current && <div className="preflight blocked settings-result">{state.approve_blockers!.join(' ')}</div>}
    {!state.c10?.current && wanted.length > 0 && <div className="actions">{wanted.map(key => <label key={key} className="acceptance-confirm"><input type="checkbox" disabled={!state.allowed} checked={Boolean(checks[key])} onChange={e => setChecks({ ...checks, [key]: e.target.checked })} />{checkLabels[key] ?? key}</label>)}</div>}
    <div className="actions"><button className="button-primary" disabled={busy || !state.allowed || !state.c10_hash || Boolean(state.c10?.current) || wanted.some(key => !checks[key])} onClick={() => act(async () => { await api(`/episodes/${episodeId}/c10/approve`, { expected_hash: state.c10_hash, checks: Object.fromEntries(wanted.map(key => [key, true])) }); setChecks({}) }, t('Đã xác nhận xuất phim (C10). Tập này hoàn tất.'))}>{t('Xác nhận đã xuất phim (C10)')}</button></div>
  </section>
}
