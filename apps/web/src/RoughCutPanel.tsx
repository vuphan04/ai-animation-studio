import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import { errorLabel, stateLabel } from './uiText'
import { t } from './i18n'

type Cut = { id: string; created_at: string; frames: number; seconds: number; current: boolean } | null
type RcShot = { shot_id: string; route: string; length_frames: number; start_seconds: number; source: { ready: boolean; kind: string; label: string } }
type Scene = { scene_key: string; heading: string; frames: number; seconds: number; shots: RcShot[]; ready: boolean; input_hash: string; rough_cut: Cut; lock: { locked: boolean; stale: boolean; created_at: string } | null }
type RoughState = { ready: boolean; blockers: string[]; scenes: Scene[]; jobs: { id: string; scene_key: string; state: string; error: string | null }[]; ffmpeg_configured: boolean; whole: { ready: boolean; input_hash: string; frames: number; seconds: number; rough_cut: Cut } | null; c7: { sequence: number; created_at: string; current: boolean } | null; lock_hash?: string; approve_blockers: string[]; allowed: boolean; summary: { scenes: number; locked: number; shots: number; shots_ready: number }; size?: { width: number; height: number }; final_note?: string }

const checkLabels: [string, string][] = [['rhythm', t('Nhịp')], ['continuity', t('Độ liền mạch')], ['eyelines', t('Hướng nhìn')], ['look', t('Màu và style giữa các shot')]]
const routeShort: Record<string, string> = { UE: 'UE', AI: 'AI', HYBRID: 'Hybrid' }
const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${(seconds % 60).toFixed(1).padStart(4, '0')}`

export default function RoughCutPanel({ episodeId, refreshKey }: { episodeId: string; refreshKey: number }) {
  const [state, setState] = useState<RoughState | null>(null)
  const [checks, setChecks] = useState<Record<string, Record<string, boolean>>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const players = useRef<Record<string, HTMLVideoElement | null>>({})

  const load = useCallback(async () => setState(await api<RoughState>(`/episodes/${episodeId}/rough-cut`)), [episodeId])
  useEffect(() => { setChecks({}); setError(''); setNotice(''); load().catch(e => setError(errorLabel(e))) }, [load, refreshKey])
  const working = state?.jobs.some(j => ['QUEUED', 'RUNNING'].includes(j.state)) ?? false
  useEffect(() => {
    if (!working) return
    const timer = setInterval(() => load().catch(e => setError(errorLabel(e))), 3000)
    return () => clearInterval(timer)
  }, [working, load])

  async function act(action: () => Promise<unknown>, message: string) {
    setBusy(true); setError(''); setNotice('')
    try { await action(); await load(); setNotice(message) } catch (e) { setError(errorLabel(e)) } finally { setBusy(false) }
  }
  const seek = (key: string, seconds: number) => { const player = players.current[key]; if (player) { player.currentTime = seconds; player.play().catch(() => undefined) } }
  const building = (key: string) => state?.jobs.some(j => j.scene_key === key && ['QUEUED', 'RUNNING'].includes(j.state)) ?? false
  const failed = (key: string) => { const job = state?.jobs.find(j => j.scene_key === key); return job && job.state === 'BLOCKED' ? job.error : null }

  const title = <div><h3>{t('C7 · Rough cut → bạn duyệt → khóa dựng (Picture Lock)')}</h3><p>{t('App ghép proxy UE với draft AI/Hybrid theo scene, đúng độ dài shot đã duyệt, kèm thoại. Bạn duyệt nhịp, độ liền mạch, hướng nhìn, màu và style giữa các shot rồi khóa dựng. Chỉ shot đã khóa mới được render hoặc tạo final.')}</p></div>
  if (!state) return <section className="panel"><div className="panel-heading">{title}</div>{error && <div className="alert error" role="alert">{error}</div>}</section>

  return <section className="panel"><div className="panel-heading">{title}<span className={`status ${state.c7?.current ? 'success' : state.c7 ? 'warning' : 'neutral'}`}>{state.c7?.current ? t('Đã khóa dựng cả tập · lần {0}', state.c7.sequence) : state.c7 ? t('Đã đổi sau khi khóa dựng') : t('{0}/{1} scene đã khóa', state.summary.locked, state.summary.scenes)}</span></div>
    {error && <div className="alert error" role="alert">{error}</div>}
    {notice && <div className="alert notice" role="status">{notice}</div>}
    {!state.ready && <div className="preflight blocked settings-result">{state.blockers.join(' ')}</div>}
    {state.ready && <>
      <div className="info-line"><span>{state.summary.shots_ready}/{state.summary.shots}{t(' shot đã chốt C6 · rough cut ')}{state.size?.width}×{state.size?.height}</span><strong>{state.ffmpeg_configured ? t('FFmpeg đã cấu hình') : t('Chưa cấu hình FFmpeg')}</strong></div>
      {!state.ffmpeg_configured && <div className="preflight blocked settings-result">{t('Cấu hình FFmpeg ở Cài đặt → Công cụ xử lý video để ghép rough cut.')}</div>}
      {state.scenes.map(scene => { const marks = checks[scene.scene_key] ?? {}; const locked = Boolean(scene.lock?.locked); return <details key={scene.scene_key} className="scene-card" open={!locked}><summary><strong>{scene.scene_key} · {scene.heading}</strong><span className="muted">{scene.shots.length} shot · {clock(scene.seconds)} · {scene.shots.map(s => routeShort[s.route]).join(' · ')}</span><span className={`status ${locked ? 'success' : scene.lock?.stale || !scene.ready ? 'warning' : 'neutral'}`}>{locked ? t('Đã khóa dựng') : scene.lock?.stale ? t('Shot đã đổi sau khi khóa') : !scene.ready ? t('Còn shot chưa chốt C6') : scene.rough_cut?.current ? t('Chờ bạn duyệt') : t('Chưa ghép rough cut')}</span></summary>
        <div className="table-wrap"><table><thead><tr><th>Shot</th><th>{t('Bắt đầu')}</th><th>{t('Độ dài')}</th><th>{t('Nguồn')}</th></tr></thead><tbody>{scene.shots.map(shot => <tr key={shot.shot_id}><td className="strong">{shot.shot_id} · {routeShort[shot.route]}</td><td>{scene.rough_cut?.current ? <button type="button" className="button-quiet" onClick={() => seek(scene.scene_key, shot.start_seconds)}>{clock(shot.start_seconds)} ▶</button> : clock(shot.start_seconds)}</td><td>{shot.length_frames} frame</td><td><span className={`status ${shot.source.ready ? 'success' : 'warning'}`}>{shot.source.label}</span></td></tr>)}</tbody></table></div>
        <div className="actions"><button className={scene.rough_cut?.current ? '' : 'button-primary'} disabled={busy || building(scene.scene_key) || !scene.ready || !state.ffmpeg_configured || Boolean(scene.rough_cut?.current)} onClick={() => act(() => api(`/episodes/${episodeId}/rough-cut/build`, { scene_key: scene.scene_key }), t('Đã xếp việc ghép rough cut của {0}.', scene.scene_key))}>{building(scene.scene_key) ? t('Đang ghép…') : scene.rough_cut ? t('Ghép lại rough cut của scene') : t('Ghép rough cut của scene')}</button></div>
        {failed(scene.scene_key) && <p className="row-error">{failed(scene.scene_key)}</p>}
        {scene.rough_cut && <><video key={scene.rough_cut.id} ref={element => { players.current[scene.scene_key] = element }} className="rough-video" controls preload="metadata" src={`/api/rough-cut/files/${scene.rough_cut.id}`} aria-label={t('Rough cut của {0}', scene.scene_key)} /><p className="help">{t('Ghép lúc ')}{scene.rough_cut.created_at.slice(0, 16).replace('T', ' ')} · {clock(scene.rough_cut.seconds)}{scene.rough_cut.current ? '' : t(' · thuộc bộ shot cũ, ghép lại trước khi khóa dựng')}</p></>}
        {locked ? <div className="actions"><button disabled={busy} onClick={() => act(() => api(`/episodes/${episodeId}/rough-cut/scenes/${scene.scene_key}/unlock`, {}), t('Đã mở khóa dựng của {0}.', scene.scene_key))}>{t('Mở khóa dựng scene này')}</button></div> : <>
          <div className="actions">{checkLabels.map(([key, label]) => <label key={key} className="acceptance-confirm"><input type="checkbox" disabled={!scene.rough_cut?.current} checked={Boolean(marks[key])} onChange={e => setChecks({ ...checks, [scene.scene_key]: { ...marks, [key]: e.target.checked } })} />{label}</label>)}</div>
          <div className="actions"><button className="button-primary" disabled={busy || !scene.rough_cut?.current || checkLabels.some(([key]) => !marks[key])} onClick={() => act(async () => { await api(`/episodes/${episodeId}/rough-cut/scenes/${scene.scene_key}/lock`, { expected_hash: scene.input_hash, checks: Object.fromEntries(checkLabels.map(([key]) => [key, true])) }); setChecks(Object.fromEntries(Object.entries(checks).filter(([k]) => k !== scene.scene_key))) }, t('Đã khóa dựng {0}.', scene.scene_key))}>{t('Khóa dựng scene này')}</button></div>
        </>}
      </details> })}
      {state.whole && <>
        <h4>{t('Cả tập')}</h4>
        <div className="actions"><button disabled={busy || building('') || !state.whole.ready || !state.ffmpeg_configured || Boolean(state.whole.rough_cut?.current)} onClick={() => act(() => api(`/episodes/${episodeId}/rough-cut/build`, { scene_key: null }), t('Đã xếp việc ghép rough cut cả tập.'))}>{building('') ? t('Đang ghép…') : state.whole.rough_cut ? t('Ghép lại rough cut cả tập') : t('Ghép rough cut cả tập')}</button><span className="help">{clock(state.whole.seconds)} · {state.whole.frames}{t(' frame theo timing đã duyệt')}</span></div>
        {failed('') && <p className="row-error">{failed('')}</p>}
        {state.whole.rough_cut && <><video key={state.whole.rough_cut.id} className="rough-video" controls preload="metadata" src={`/api/rough-cut/files/${state.whole.rough_cut.id}`} aria-label={t('Rough cut cả tập')} /><p className="help">{t('Ghép lúc ')}{state.whole.rough_cut.created_at.slice(0, 16).replace('T', ' ')}{state.whole.rough_cut.current ? '' : t(' · thuộc bộ shot cũ')}</p></>}
      </>}
      {state.approve_blockers.length > 0 && !state.c7?.current && <div className="preflight blocked settings-result">{state.approve_blockers.join(' ')}</div>}
      <div className="actions"><button className="button-primary" disabled={busy || !state.allowed || Boolean(state.c7?.current)} onClick={() => act(() => api(`/episodes/${episodeId}/c7/approve`, { expected_hash: state.lock_hash }), t('Đã khóa dựng cả tập (C7).'))}>{t('Khóa dựng cả tập (C7)')}</button></div>
      {state.jobs.filter(j => j.state === 'RUNNING').map(j => <p key={j.id} className="help">{stateLabel(j.state)}{t(': đang ghép ')}{j.scene_key || t('cả tập')}…</p>)}
      <p className="help">{state.final_note}{t(' Rough cut là bản xem thử ở độ phân giải proxy; draft AI ngắn hơn shot được giữ frame cuối, dài hơn thì cắt ở đúng độ dài shot.')}</p>
    </>}
  </section>
}
