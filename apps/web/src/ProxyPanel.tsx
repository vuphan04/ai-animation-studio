import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { errorLabel, stateLabel } from './uiText'
import { t } from './i18n'

type Sub = 'HAIR' | 'CLOTH' | 'VFX'
type CacheItem = { status: string; note: string; current: boolean } | null
type ProxyShot = { shot_id: string; route: string; description: string; length_frames: number; seconds: number; c5_locked: boolean; build_hash: string; cache: Record<Sub, CacheItem>; cache_ready: boolean; proxy: { id: string; version: number; provenance: string; frames: number; current: boolean; file_ok: boolean; inspection: { cache_tracks?: Record<string, boolean> } } | null; proxy_usable: boolean; issues: string[]; warnings: string[]; c6_hash: string; c6: { locked: boolean; stale: boolean } | null }
type ProxyJob = { id: string; state: string; stage: string; error: string | null; shots: string[]; failed: { shot_id: string; error: string }[] }
type ProxyState = { ready: boolean; blockers: string[]; shots: ProxyShot[]; jobs: ProxyJob[]; ffmpeg_configured: boolean; unreal_configured: boolean; summary: { shots: number; with_proxy: number; locked: number }; proxy_size?: { width: number; height: number }; cache_note?: string; final_note?: string; import_note?: string | null }
type Draft = Record<Sub, { status: string; note: string }>

const subs: [Sub, string][] = [['HAIR', t('Tóc (groom)')], ['CLOTH', t('Vải (cloth)')], ['VFX', t('VFX quan trọng')]]
const stageLabel: Record<string, string> = { PREPARE: t('Unreal kiểm tra sequence và lập hàng đợi render'), RENDER: t('Unreal đang render frame'), ENCODE: t('FFmpeg ghép frame thành proxy') }

export default function ProxyPanel({ episodeId, refreshKey, onChanged }: { episodeId: string; refreshKey: number; onChanged: () => void }) {
  const [state, setState] = useState<ProxyState | null>(null)
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const [stopped, setStopped] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(async () => setState(await api<ProxyState>(`/episodes/${episodeId}/proxy`)), [episodeId])
  useEffect(() => { setDrafts({}); setError(''); setNotice(''); load().catch(e => setError(errorLabel(e))) }, [load, refreshKey])
  const working = state?.jobs.some(j => ['QUEUED', 'RUNNING'].includes(j.state)) ?? false
  useEffect(() => {
    if (!working) return
    const timer = setInterval(() => load().catch(e => setError(errorLabel(e))), 3000)
    return () => clearInterval(timer)
  }, [working, load])

  async function act(action: () => Promise<unknown>, message: string) {
    setBusy(true); setError(''); setNotice('')
    try { await action(); await load(); setNotice(message); onChanged() } catch (e) { setError(errorLabel(e)) } finally { setBusy(false) }
  }

  const title = <div><h3>{t('09 / C6 · Cache và proxy của nhánh UE')}</h3><p>{t('Sau khi chốt C5: bake/cache tóc, vải và VFX trong Unreal, khai báo lại ở đây, rồi app render proxy độ phân giải thấp cho rough cut. Chưa render final ở bước này.')}</p></div>
  if (!state) return <section className="panel"><div className="panel-heading">{title}</div>{error && <div className="alert error" role="alert">{error}</div>}</section>
  const stuck = state.jobs.find(j => j.state === 'RECONCILING')
  const draft = (shot: ProxyShot): Draft => drafts[shot.shot_id] ?? Object.fromEntries(subs.map(([key]) => [key, { status: shot.cache[key]?.current ? shot.cache[key]!.status : '', note: shot.cache[key]?.current ? shot.cache[key]!.note : '' }])) as Draft
  const ueShots = state.shots.filter(s => s.route === 'UE').length

  return <section className="panel"><div className="panel-heading">{title}<span className={`status ${ueShots > 0 && state.summary.locked === ueShots ? 'success' : 'neutral'}`}>{state.summary.locked}/{ueShots}{t(' shot UE đã chốt C6')}</span></div>
    {error && <div className="alert error" role="alert">{error}</div>}
    {notice && <div className="alert notice" role="status">{notice}</div>}
    {!state.ready && <div className="preflight blocked settings-result">{state.blockers.join(' ')}</div>}
    {state.ready && state.shots.length === 0 && <p className="help">{t('Tập này không có shot UE hoặc Hybrid.')}</p>}
    {state.ready && state.shots.length > 0 && <>
      <div className="info-line"><span>Proxy {state.proxy_size?.width}×{state.proxy_size?.height} · {state.summary.with_proxy}/{state.summary.shots}{t(' shot có proxy đúng bản đã chốt C5')}</span><strong>{state.ffmpeg_configured ? t('FFmpeg đã cấu hình') : t('Chưa cấu hình FFmpeg')}</strong></div>
      {!state.ffmpeg_configured && <div className="preflight blocked settings-result">{t('Cấu hình FFmpeg ở Cài đặt → Công cụ xử lý video; app dùng FFmpeg để ghép frame của Unreal thành proxy.')}</div>}
      <div className="actions"><button className="button-primary" disabled={busy || working || Boolean(stuck) || !state.ffmpeg_configured || !state.unreal_configured || !state.shots.some(s => !s.issues.length && !s.proxy_usable)} onClick={() => act(() => api(`/episodes/${episodeId}/proxy/render`, { shot_ids: null }), t('Đã xếp việc render proxy; Unreal chạy nền, GPU chỉ chạy một việc nặng mỗi lúc.'))}>{t('Render proxy cho các shot đã khai báo cache')}</button></div>
      {stuck && <div className="preflight blocked settings-result"><strong>{t('Lần render trước bị gián đoạn.')}</strong>{t(' Đóng cửa sổ Unreal của lần render đó rồi xác nhận. ')}<label className="acceptance-confirm"><input type="checkbox" checked={stopped} onChange={e => setStopped(e.target.checked)} />{t('Tôi đã đóng Unreal của lần render cũ')}</label> <button disabled={busy || !stopped} onClick={() => act(async () => { await api(`/proxy/jobs/${stuck.id}/release`, { worker_stopped: true }); setStopped(false) }, t('Đã xác nhận; có thể render lại.'))}>{t('Xác nhận và cho render lại')}</button></div>}
      {state.jobs.slice(0, 3).map(j => <div key={j.id} className="voice-job-list"><div><span className={`status ${j.state === 'COMPLETED' ? 'success' : ['BLOCKED', 'RECONCILING'].includes(j.state) ? 'warning' : 'neutral'}`}>{stateLabel(j.state)}</span> Render proxy · {j.shots.length} shot{j.state === 'RUNNING' ? ` · ${stageLabel[j.stage] ?? j.stage}` : ''}{j.error && <small className="row-error"> {j.error}</small>}{j.failed.map(f => <small key={f.shot_id + f.error} className="row-error"> {f.shot_id}: {f.error}</small>)}</div></div>)}
      {state.shots.map(shot => { const d = draft(shot); const dirty = Boolean(drafts[shot.shot_id]); const complete = subs.every(([key]) => d[key].status); return <details key={shot.shot_id} className="scene-card" open={!shot.c6?.locked && shot.c5_locked}><summary><strong>{shot.shot_id} · {shot.route === 'HYBRID' ? t('nguồn UE của Hybrid') : 'UE'}</strong><span className="muted">{shot.length_frames} frame · {shot.proxy ? t('proxy bản {0}{1}', shot.proxy.version, shot.proxy.current ? '' : t(' (bản cũ)')) : t('chưa có proxy')}</span><span className={`status ${shot.c6?.locked ? 'success' : shot.issues.length || shot.c6?.stale ? 'warning' : 'neutral'}`}>{shot.c6?.locked ? t('Đã chốt C6') : shot.c6?.stale ? t('Đã đổi sau khi chốt') : !shot.c5_locked ? t('Chờ chốt C5') : !shot.cache_ready ? t('Chờ khai báo cache') : shot.proxy_usable ? (shot.route === 'HYBRID' ? t('Có nguồn UE cho draft AI') : t('Chờ bạn chốt C6')) : t('Chờ render proxy')}</span></summary>
        <p className="help">{shot.description}</p>
        {shot.issues.map(i => <p key={i} className="row-error">{i}</p>)}
        {shot.c5_locked && <>
          <h5>Cache simulation</h5>
          <div className="planner-fields">{subs.map(([key, label]) => <label key={key} className="field">{label}<select value={d[key].status} disabled={Boolean(shot.c6?.locked)} onChange={e => setDrafts({ ...drafts, [shot.shot_id]: { ...d, [key]: { ...d[key], status: e.target.value } } })}><option value="">{t('Chưa khai báo')}</option><option value="BAKED">{t('Đã bake cache trong Unreal')}</option><option value="NOT_APPLICABLE">{t('Không áp dụng cho shot này')}</option></select><input value={d[key].note} maxLength={200} disabled={Boolean(shot.c6?.locked)} onChange={e => setDrafts({ ...drafts, [shot.shot_id]: { ...d, [key]: { ...d[key], note: e.target.value } } })} placeholder={t('Ghi chú (tùy chọn)')} /></label>)}</div>
          <div className="actions"><button disabled={busy || !dirty || !complete} onClick={() => act(async () => { await api(`/episodes/${episodeId}/proxy/shots/${shot.shot_id}/cache`, { expected_build_hash: shot.build_hash, declarations: d }); setDrafts(Object.fromEntries(Object.entries(drafts).filter(([k]) => k !== shot.shot_id))) }, t('Đã lưu khai báo cache của {0}.', shot.shot_id))}>{t('Lưu khai báo cache')}</button></div>
          {shot.proxy?.inspection.cache_tracks && <p className="help">{t('Unreal báo track cache trong sequence: ')}{subs.map(([key, label]) => `${label}: ${shot.proxy!.inspection.cache_tracks![key] ? t('có') : t('không thấy')}`).join(' · ')}.</p>}
          {shot.warnings.map(w => <p key={w} className="row-error">{w}</p>)}
          <h5>Proxy</h5>
          {shot.proxy && (shot.proxy.file_ok ? <video className="draft-video" controls preload="none" src={`/api/proxy/files/${shot.proxy.id}`} aria-label={t('Xem proxy của {0}', shot.shot_id)} /> : <p className="row-error">{t('File proxy thiếu trong thư mục dự án.')}</p>)}
          {shot.proxy && <p className="help">{t('Bản ')}{shot.proxy.version} · {shot.proxy.frames} frame · {shot.proxy.provenance === 'UE_MRQ' ? t('app render bằng Movie Render Queue') : t('bạn render tay và nhập vào')}{shot.proxy.current ? '' : t(' · không còn khớp bản đã chốt C5, cần render lại')}</p>}
          <div className="actions"><button disabled={busy || working || Boolean(stuck) || shot.issues.length > 0 || Boolean(shot.c6?.locked) || !state.ffmpeg_configured || !state.unreal_configured} onClick={() => act(() => api(`/episodes/${episodeId}/proxy/render`, { shot_ids: [shot.shot_id] }), t('Đã xếp việc render proxy cho {0}.', shot.shot_id))}>{shot.proxy ? t('Render lại proxy shot này') : t('Render proxy shot này')}</button><button disabled={busy || shot.issues.length > 0 || Boolean(shot.c6?.locked) || !state.ffmpeg_configured} onClick={() => { setNotice(t('Đang mở cửa sổ Windows; kiểm tra thanh tác vụ nếu chưa thấy.')); act(() => api(`/episodes/${episodeId}/proxy/shots/${shot.shot_id}/import/browse`, {}), t('Đã nhập proxy render tay cho {0}.', shot.shot_id)) }}>{t('Nhập proxy đã render tay…')}</button>
            {shot.route === 'UE' && (shot.c6?.locked ? <button disabled={busy} onClick={() => act(() => api(`/episodes/${episodeId}/proxy/shots/${shot.shot_id}/c6/unlock`, {}), t('Đã mở khóa C6 của {0}.', shot.shot_id))}>{t('Mở khóa C6')}</button> : <button className="button-primary" disabled={busy || dirty || shot.issues.length > 0 || !shot.proxy_usable} onClick={() => act(() => api(`/episodes/${episodeId}/proxy/shots/${shot.shot_id}/c6`, { expected_hash: shot.c6_hash }), t('Đã chốt cache và proxy của {0} (C6).', shot.shot_id))}>{t('Chốt cache và proxy (C6)')}</button>)}</div>
          {shot.route === 'HYBRID' && <p className="help">{t('Proxy của shot Hybrid là nguồn UE cho draft AI: app lấy frame đầu và đăng ký làm đầu vào ở nhánh AI. Shot Hybrid chốt C6 bằng draft AI.')}</p>}
        </>}
      </details> })}
      <p className="help">{state.cache_note} {state.final_note}</p>
    </>}
  </section>
}
