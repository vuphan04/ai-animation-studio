import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import { errorLabel, stateLabel } from './uiText'
import { t } from './i18n'

type Clip = { id: string; kind: string; label: string; shot_id: string; offset_frames: number; length_frames: number | null; play_frames: number; gain_db: number; fade_in_frames: number; fade_out_frames: number; provenance: string; prompt: string | null; duration_seconds: number; start_frame: number | null; start_seconds: number | null; enabled: boolean; stale: boolean; issues: string[]; revision: number; created_at: string }
type SoundShot = { shot_id: string; scene_key: string; route: string; description: string; start_frame: number; start_seconds: number; length_frames: number; seconds: number; default_prompt: string; has_picture: boolean; foley: Clip[]; foley_job: { id: string; state: string; error: string | null } | null }
type Build = { id: string; version: number; created_at: string; loudness: { integrated_lufs: number | null; true_peak_dbtp: number | null; range_lu: number | null }; stems: { role: string; label: string }[]; seconds: number; file_ok: boolean; current: boolean }
type SoundState = {
  ready: boolean; blockers: string[]; shots: SoundShot[]; clips: Clip[]; build: Build | null; job: { id: string; state: string; error: string | null } | null
  c9: { sequence: number; created_at: string; current: boolean } | null; ffmpeg_configured: boolean; foley_tool: { configured: boolean; error: string | null }; issues: string[]
  summary: { shots: number; foley: number; sfx: number; ambience: number; music: number; lines: number }; approve_blockers: string[]; allowed: boolean
  input_hash?: string; total_frames?: number; seconds?: number; notes?: { mix: string; foley: string }
}
type Edit = { shot_id: string; offset: string; length: string; gain: string; fadeIn: string; fadeOut: string }

const kindLabel: Record<string, string> = { FOLEY: 'Foley', SFX: 'SFX', AMBIENCE: 'Ambience', MUSIC: t('Nhạc') }
const provenanceLabel: Record<string, string> = { HUNYUAN_FOLEY_LOCAL: t('HunyuanVideo-Foley trên máy này'), OPERATOR_FILE: t('file bạn nhập') }
const checkLabels: [string, string][] = [['dialogue', t('Thoại rõ, không bị che')], ['sync', t('Foley và SFX khớp hình')], ['balance', t('Ambience và nhạc cân bằng với thoại')], ['clean', t('Không rè, vỡ tiếng hay cắt cụt')]]
const routeShort: Record<string, string> = { UE: 'UE', AI: 'AI', HYBRID: 'Hybrid' }
const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${(seconds % 60).toFixed(1).padStart(4, '0')}`
const stamp = (value: string) => value.slice(0, 16).replace('T', ' ')
const level = (value: number | null, unit: string) => value === null ? t('im lặng') : `${value.toFixed(1)} ${unit}`
const editOf = (clip: Clip): Edit => ({ shot_id: clip.shot_id, offset: String(clip.offset_frames), length: clip.length_frames === null ? '' : String(clip.length_frames), gain: String(clip.gain_db), fadeIn: String(clip.fade_in_frames), fadeOut: String(clip.fade_out_frames) })

export default function SoundPanel({ episodeId, refreshKey, onChanged }: { episodeId: string; refreshKey: number; onChanged: () => void }) {
  const [state, setState] = useState<SoundState | null>(null)
  const [prompts, setPrompts] = useState<Record<string, string>>({})
  const [edits, setEdits] = useState<Record<string, Edit>>({})
  const [adding, setAdding] = useState({ kind: 'SFX', shot_id: '' })
  const [checks, setChecks] = useState<Record<string, boolean>>({})
  const [stopped, setStopped] = useState<Record<string, boolean>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const pictures = useRef<Record<string, HTMLVideoElement | null>>({})
  const sounds = useRef<Record<string, HTMLAudioElement | null>>({})

  const load = useCallback(async () => setState(await api<SoundState>(`/episodes/${episodeId}/sound`)), [episodeId])
  useEffect(() => { setPrompts({}); setEdits({}); setChecks({}); setError(''); setNotice(''); load().catch(e => setError(errorLabel(e))) }, [load, refreshKey])
  const working = Boolean(state && ((state.job && ['QUEUED', 'RUNNING'].includes(state.job.state)) || state.shots.some(s => s.foley_job && ['QUEUED', 'RUNNING'].includes(s.foley_job.state))))
  useEffect(() => {
    if (!working) return
    const timer = setInterval(() => load().catch(e => setError(errorLabel(e))), 4000)
    return () => clearInterval(timer)
  }, [working, load])

  async function act(action: () => Promise<unknown>, message: string) {
    setBusy(true); setError(''); setNotice('')
    try { await action(); await load(); setNotice(message); onChanged() } catch (e) { setError(errorLabel(e)) } finally { setBusy(false) }
  }
  const body = (clip: Clip, edit: Edit, enabled: boolean) => ({ expected_revision: clip.revision, shot_id: edit.shot_id, offset_frames: Math.round(Number(edit.offset) || 0), length_frames: edit.length.trim() === '' ? null : Math.round(Number(edit.length)), gain_db: Number(edit.gain) || 0, fade_in_frames: Math.round(Number(edit.fadeIn) || 0), fade_out_frames: Math.round(Number(edit.fadeOut) || 0), enabled })
  const save = (clip: Clip, enabled: boolean, message: string) => act(async () => { await api(`/sound/clips/${clip.id}`, body(clip, edits[clip.id] ?? editOf(clip), enabled)); setEdits(Object.fromEntries(Object.entries(edits).filter(([k]) => k !== clip.id))) }, message)
  const playWith = (shotId: string, clipId: string) => { const picture = pictures.current[shotId]; const sound = sounds.current[clipId]; if (picture && sound) { picture.currentTime = 0; sound.currentTime = 0; picture.play().catch(() => undefined); sound.play().catch(() => undefined) } }

  const title = <div><h3>{t('12 / C9 · Foley và âm thanh → bạn nghe và duyệt')}</h3><p>{t('Làm trên hình đã khóa dựng. Thoại giữ nguyên các take ElevenLabs đã duyệt ở C4; Foley tạo theo từng shot bằng HunyuanVideo-Foley trên máy này; SFX, ambience và nhạc là file bạn đưa vào. App đặt mỗi lớp vào một stem riêng, đúng độ dài hình, rồi bạn nghe cùng hình và duyệt.')}</p></div>
  if (!state) return <section className="panel"><div className="panel-heading">{title}</div>{error && <div className="alert error" role="alert">{error}</div>}</section>
  const building = Boolean(state.job && ['QUEUED', 'RUNNING'].includes(state.job.state))
  const build = state.build

  return <section className="panel"><div className="panel-heading">{title}<span className={`status ${state.c9?.current ? 'success' : state.c9 ? 'warning' : 'neutral'}`}>{state.c9?.current ? t('Đã duyệt âm thanh · lần {0}', state.c9.sequence) : state.c9 ? t('Đã đổi sau khi duyệt') : t('Chưa duyệt')}</span></div>
    {error && <div className="alert error" role="alert">{error}</div>}
    {notice && <div className="alert notice" role="status">{notice}</div>}
    {!state.ready && <div className="preflight blocked settings-result">{state.blockers.join(' ')}</div>}
    {state.ready && <>
      <div className="info-line"><span>{t('Thoại: ')}{state.summary.lines}{t(' câu theo timing C4 · Foley: ')}{state.summary.foley}/{state.summary.shots} shot · SFX {state.summary.sfx} · ambience {state.summary.ambience}{t(' · nhạc ')}{state.summary.music}{t(' · dài ')}{clock(state.seconds ?? 0)}</span><strong>{state.ffmpeg_configured ? t('FFmpeg đã cấu hình') : t('Chưa cấu hình FFmpeg')}</strong></div>
      {!state.ffmpeg_configured && <div className="preflight blocked settings-result">{t('Cấu hình FFmpeg ở Cài đặt → Công cụ xử lý video để nhập file âm thanh và ghép stem.')}</div>}

      <h4>{t('Foley theo shot')}</h4>
      <p className="help">{state.foley_tool.configured ? t('HunyuanVideo-Foley đã cấu hình trên máy này.') : t('HunyuanVideo-Foley chưa cấu hình (Cài đặt → Foley local){0} Vẫn nhập được Foley làm tay.', state.foley_tool.error ? `: ${state.foley_tool.error}` : '.')} {state.notes?.foley}{t(' Shot không cần Foley thì để trống.')}</p>
      {state.shots.map(shot => {
        const used = shot.foley.find(f => f.enabled); const job = shot.foley_job; const active = Boolean(job && ['QUEUED', 'RUNNING'].includes(job.state)); const prompt = prompts[shot.shot_id] ?? shot.foley[0]?.prompt ?? shot.default_prompt
        return <details key={shot.shot_id} className="scene-card"><summary><strong>{shot.shot_id} · {routeShort[shot.route]}</strong><span className="muted">{t('bắt đầu ')}{clock(shot.start_seconds)} · {shot.length_frames} frame · {shot.foley.length}{t(' take Foley')}</span><span className={`status ${used && !used.stale ? 'success' : used?.stale ? 'warning' : 'neutral'}`}>{active ? t('Đang tạo Foley') : used?.stale ? t('Hình đã đổi sau khi tạo Foley') : used ? t('Đang dùng Foley') : shot.foley.length ? t('Có take · chưa chọn') : t('Chưa có Foley')}</span></summary>
          <p className="help">{shot.description}</p>
          {shot.has_picture ? <video ref={element => { pictures.current[shot.shot_id] = element }} className="draft-video" controls muted preload="none" src={`/api/episodes/${episodeId}/rough-cut/segments/${shot.shot_id}`} aria-label={t('Hình đã khóa của {0}', shot.shot_id)} /> : <p className="row-error">{t('Thiếu đoạn rough cut của shot; ghép lại rough cut của scene ở tab Dựng shot.')}</p>}
          <label className="field">{t('Mô tả âm thanh cần tạo cho shot (tiếng Anh)')}<textarea rows={2} maxLength={500} value={prompt} onChange={e => setPrompts({ ...prompts, [shot.shot_id]: e.target.value })} /></label>
          <div className="actions"><button className="button-primary" disabled={busy || active || job?.state === 'RECONCILING' || !state.foley_tool.configured || !shot.has_picture || !prompt.trim() || !state.ffmpeg_configured} onClick={() => act(() => api(`/episodes/${episodeId}/sound/foley`, { shot_id: shot.shot_id, prompt }), t('Đã xếp việc tạo Foley cho {0}; chạy khi GPU rảnh.', shot.shot_id))}>{shot.foley.length ? t('Tạo take Foley mới') : t('Tạo Foley')}</button>
            <button disabled={busy || !state.ffmpeg_configured} onClick={() => { setNotice(t('Đang mở cửa sổ Windows; kiểm tra thanh tác vụ nếu chưa thấy.')); act(() => api(`/episodes/${episodeId}/sound/clips/browse`, { kind: 'FOLEY', shot_id: shot.shot_id }), t('Đã nhập Foley làm tay cho {0}; nghe rồi chọn dùng.', shot.shot_id)) }}>{t('Nhập Foley làm tay…')}</button></div>
          {job && job.state !== 'COMPLETED' && <p className={['BLOCKED', 'RECONCILING'].includes(job.state) ? 'row-error' : 'help'}>{stateLabel(job.state)}{job.state === 'QUEUED' ? t(' · chờ GPU rảnh') : ''}{job.error ? ` · ${job.error}` : ''}</p>}
          {job?.state === 'RECONCILING' && <div className="actions"><label className="acceptance-confirm"><input type="checkbox" checked={Boolean(stopped[job.id])} onChange={e => setStopped({ ...stopped, [job.id]: e.target.checked })} />{t('Tôi đã đóng tiến trình Foley của lần chạy cũ')}</label><button disabled={busy || !stopped[job.id]} onClick={() => act(() => api(`/sound/foley-jobs/${job.id}/release`, { worker_stopped: true }), t('Đã xác nhận; có thể tạo lại Foley.'))}>{t('Xác nhận và cho tạo lại')}</button></div>}
          {shot.foley.map(take => <div key={take.id} className="route-shot">
            <div className="route-shot-head"><strong>{take.label}</strong><span className="muted">{provenanceLabel[take.provenance] ?? take.provenance} · {take.duration_seconds}{t(' giây · ')}{stamp(take.created_at)}{take.prompt ? ` · “${take.prompt}”` : ''}</span><span className={`status ${take.enabled && !take.stale ? 'success' : take.stale ? 'warning' : 'neutral'}`}>{take.enabled ? t('Đang dùng') : t('Chưa dùng')}</span></div>
            <div className="take-row"><audio ref={element => { sounds.current[take.id] = element }} controls preload="none" src={`/api/sound/clips/${take.id}/file`} aria-label={`Nghe ${take.label}`} /><button className="button-quiet" disabled={!shot.has_picture} onClick={() => playWith(shot.shot_id, take.id)}>{t('Phát cùng hình')}</button></div>
            {take.issues.map(i => <p key={i} className="row-error">{i}</p>)}
            <div className="actions">{!take.enabled && <button className="button-primary" disabled={busy} onClick={() => save(take, true, t('Đã chọn {0} cho track Foley.', take.label))}>{t('Tôi đã nghe · dùng take này')}</button>}
              {take.enabled && <button disabled={busy} onClick={() => save(take, false, t('Đã bỏ {0} khỏi track Foley.', take.label))}>{t('Bỏ dùng')}</button>}
              {take.stale && <button disabled={busy} onClick={() => act(() => api(`/sound/clips/${take.id}/keep`, { expected_revision: take.revision }), t('Đã xác nhận Foley còn khớp hình mới.'))}>{t('Tôi đã nghe lại · vẫn khớp hình')}</button>}
              <button className="button-quiet" disabled={busy} onClick={() => act(() => api(`/sound/clips/${take.id}/delete`, { expected_revision: take.revision }), t('Đã xóa {0}.', take.label))}>{t('Xóa take')}</button></div>
          </div>)}
        </details>
      })}

      <h4>{t('SFX, ambience và nhạc')}</h4>
      <div className="form-row"><label className="field">{t('Loại')}<select value={adding.kind} onChange={e => setAdding({ ...adding, kind: e.target.value })}>{['SFX', 'AMBIENCE', 'MUSIC'].map(k => <option key={k} value={k}>{kindLabel[k]}</option>)}</select></label>
        <label className="field grow">{t('Neo vào shot (clip di chuyển theo shot khi timing đổi)')}<select value={adding.shot_id} onChange={e => setAdding({ ...adding, shot_id: e.target.value })}><option value="">{t('Chọn shot')}</option>{state.shots.map(s => <option key={s.shot_id} value={s.shot_id}>{s.shot_id}{t(' · bắt đầu ')}{clock(s.start_seconds)}</option>)}</select></label>
        <button disabled={busy || !adding.shot_id || !state.ffmpeg_configured} onClick={() => { setNotice(t('Đang mở cửa sổ Windows; kiểm tra thanh tác vụ nếu chưa thấy.')); act(() => api(`/episodes/${episodeId}/sound/clips/browse`, adding), t('Đã thêm {0} ở đầu {1}; chỉnh vị trí và âm lượng bên dưới.', kindLabel[adding.kind], adding.shot_id)) }}>{t('Thêm file âm thanh…')}</button></div>
      {state.clips.length === 0 && <p className="help">{t('Chưa có SFX, ambience hay nhạc. File WAV, MP3, FLAC, M4A, OGG hoặc AIFF đều dùng được; app chép một bản vào thư mục dự án.')}</p>}
      {state.clips.map(clip => { const edit = edits[clip.id] ?? editOf(clip); const dirty = Boolean(edits[clip.id]); const set = (change: Partial<Edit>) => setEdits({ ...edits, [clip.id]: { ...edit, ...change } }); return <div key={clip.id} className="route-shot">
        <div className="route-shot-head"><strong>{kindLabel[clip.kind]} · {clip.label}</strong><span className="muted">{clip.start_seconds !== null ? t('bắt đầu {0} · ', clock(clip.start_seconds)) : ''}{t('phát ')}{clip.play_frames}{t(' frame · file dài ')}{clip.duration_seconds}{t(' giây')}</span><span className={`status ${clip.issues.length ? 'warning' : clip.enabled ? 'success' : 'neutral'}`}>{clip.issues.length ? t('Cần sửa') : clip.enabled ? t('Có trong stem') : t('Đang tắt')}</span></div>
        <div className="form-row">
          <label className="field">{t('Shot neo')}<select value={edit.shot_id} onChange={e => set({ shot_id: e.target.value })}>{!state.shots.some(s => s.shot_id === edit.shot_id) && <option value={edit.shot_id}>{edit.shot_id}{t(' (không còn)')}</option>}{state.shots.map(s => <option key={s.shot_id} value={s.shot_id}>{s.shot_id}</option>)}</select></label>
          <label className="field">{t('Bắt đầu sau (frame)')}<input type="number" min={0} step={1} value={edit.offset} onChange={e => set({ offset: e.target.value })} /></label>
          <label className="field">{t('Độ dài phát (frame, trống = cả file)')}<input type="number" min={1} step={1} value={edit.length} onChange={e => set({ length: e.target.value })} /></label>
          <label className="field">{t('Âm lượng (dB)')}<input type="number" min={-60} max={12} step={0.5} value={edit.gain} onChange={e => set({ gain: e.target.value })} /></label>
          <label className="field">{t('Fade vào (frame)')}<input type="number" min={0} step={1} value={edit.fadeIn} onChange={e => set({ fadeIn: e.target.value })} /></label>
          <label className="field">{t('Fade ra (frame)')}<input type="number" min={0} step={1} value={edit.fadeOut} onChange={e => set({ fadeOut: e.target.value })} /></label>
        </div>
        <div className="take-row"><audio controls preload="none" src={`/api/sound/clips/${clip.id}/file`} aria-label={`Nghe ${clip.label}`} /></div>
        {clip.issues.map(i => <p key={i} className="row-error">{i}</p>)}
        <div className="actions"><button className={dirty ? 'button-primary' : ''} disabled={busy || !dirty} onClick={() => save(clip, clip.enabled, t('Đã lưu vị trí của {0}.', clip.label))}>{t('Lưu vị trí và âm lượng')}</button>
          <button disabled={busy || dirty} onClick={() => save(clip, !clip.enabled, clip.enabled ? t('Đã tắt {0}.', clip.label) : t('Đã bật {0}.', clip.label))}>{clip.enabled ? t('Tắt clip') : t('Bật clip')}</button>
          <button className="button-quiet" disabled={busy} onClick={() => act(() => api(`/sound/clips/${clip.id}/delete`, { expected_revision: clip.revision }), t('Đã xóa {0}.', clip.label))}>{t('Xóa clip')}</button></div>
      </div> })}

      <h4>{t('Stem và bản nghe thử cùng hình')}</h4>
      <div className="actions"><button className={build?.current ? '' : 'button-primary'} disabled={busy || building || !state.ffmpeg_configured || state.issues.length > 0 || Boolean(build?.current && build.file_ok)} onClick={() => act(() => api(`/episodes/${episodeId}/sound/build`, {}), t('Đã xếp việc ghép stem và bản nghe thử.'))}>{building ? t('Đang ghép…') : build ? t('Ghép lại stem và bản nghe thử') : t('Ghép stem và bản nghe thử')}</button><span className="help">{t('5 stem WAV 48 kHz 24-bit stereo (thoại, Foley, SFX, ambience, nhạc), mỗi stem dài đúng bằng hình.')}</span></div>
      {state.job?.state === 'BLOCKED' && <p className="row-error">{state.job.error}</p>}
      {state.issues.length > 0 && <div className="preflight blocked settings-result"><strong>{t('Sửa trước khi ghép stem:')}</strong> {state.issues.join(' · ')}</div>}
      {build && <>
        {build.file_ok ? <video key={build.id} className="rough-video" controls preload="metadata" src={`/api/sound/builds/${build.id}/files/REVIEW`} aria-label={t('Bản nghe thử âm thanh cùng hình đã khóa')} /> : <p className="row-error">{t('File stem thiếu trong thư mục dự án; ghép lại.')}</p>}
        <p className="help">{t('Bản ')}{build.version}{t(' · ghép lúc ')}{stamp(build.created_at)} · {clock(build.seconds)}{t(' · mix tham chiếu ')}{level(build.loudness.integrated_lufs, 'LUFS')}, true peak {level(build.loudness.true_peak_dbtp, 'dBTP')}{build.current ? '' : t(' · thuộc âm thanh hoặc hình cũ, ghép lại trước khi duyệt')}</p>
        {build.file_ok && <details className="scene-card"><summary><strong>{t('Nghe từng stem')}</strong><span className="muted">{build.stems.map(s => s.label).join(' · ')}</span></summary>{build.stems.map(stem => <div key={stem.role} className="take-row"><strong>{stem.label}</strong><audio controls preload="none" src={`/api/sound/builds/${build.id}/files/${stem.role}`} aria-label={`Nghe stem ${stem.label}`} /></div>)}</details>}
      </>}
      {state.approve_blockers.length > 0 && !state.c9?.current && build && <div className="preflight blocked settings-result">{state.approve_blockers.join(' ')}</div>}
      {!state.c9?.current && <div className="actions">{checkLabels.map(([key, label]) => <label key={key} className="acceptance-confirm"><input type="checkbox" disabled={!state.allowed} checked={Boolean(checks[key])} onChange={e => setChecks({ ...checks, [key]: e.target.checked })} />{label}</label>)}</div>}
      <div className="actions"><button className="button-primary" disabled={busy || !state.allowed || Boolean(state.c9?.current) || checkLabels.some(([key]) => !checks[key])} onClick={() => act(async () => { await api(`/episodes/${episodeId}/c9/approve`, { expected_hash: state.input_hash, checks: Object.fromEntries(checkLabels.map(([key]) => [key, true])) }); setChecks({}) }, t('Đã duyệt âm thanh của tập (C9).'))}>{t('Duyệt âm thanh (C9)')}</button></div>
      <p className="help">{state.notes?.mix}</p>
    </>}
  </section>
}
