import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { errorLabel, stateLabel } from './uiText'
import { AssetPicker } from './SeriesPage'
import { t } from './i18n'

type Clip = { asset_path: string; blend_frames: number; asset_name: string; retarget: string | null }
type Slot = { slot_id: string; shot_id: string; character_name: string; action: string; tag: string; inferred_tag: boolean; start_frame: number; end_frame: number; frames: number; clips: Clip[]; upper_body_path: string | null; source: string; confidence: number; low_confidence: boolean; status: string; approved: boolean; stale: boolean; issues: string[]; revision: number }
type KimodoRequest = { id: string; slot_id: string; prompt: string; seconds: number; variants: number; state: string; variant_paths: string[]; selected_path: string | null; revision: number }
type Animation = { ready: boolean; blockers: string[]; slots: Slot[]; outdated: boolean; requests: KimodoRequest[]; summary: { slots: number; approved: number; missing: number; kimodo: number; low: number }; allowed: boolean; folders: string[]; library_clips: { asset_path: string; tags: string[] }[]; preview_note?: string; kimodo_note?: string }
type UeShot = { shot_id: string; route: string; description: string; length_frames: number; seconds: number; sequence_path: string; map_path: string | null; characters: string[]; lines: number; vfx_tags: string[]; camera_note: string; assembled: string; animated: string; slots_total: number; slots_pending: number; build_notes: string[]; issues: string[]; build_hash: string; c5: { locked: boolean; stale: boolean; notes: { keep: string; ai_change: string } | null } }
type UeJob = { id: string; kind: string; state: string; error: string | null; shots: string[]; failed: { shot_id: string; error: string | null }[]; retarget_notes: { output_path: string; status: string; error?: string }[] }
type UeState = { ready: boolean; blockers: string[]; shots: UeShot[]; jobs: UeJob[]; master_path: string | null; summary: { shots: number; assembled: number; animated: number; locked: number }; unreal_configured: boolean; slots_outdated?: boolean; master_stale?: boolean; manual_note?: string }
type Review = { performance: boolean; camera: boolean; layout: boolean; keep: string; ai_change: string }

const short = (path: string | null) => path ? path.split('.').pop() ?? path : '—'
const buildLabel: Record<string, string> = { NONE: t('Chưa dựng'), CURRENT: t('Đúng timing hiện tại'), STALE: t('Đã cũ · dựng lại'), NOT_NEEDED: t('Không cần') }
const retargetLabel: Record<string, string> = { DIRECT: t('gắn thẳng'), EXISTING: t('qua retargeter đã duyệt'), NEEDS_CREATE: t('app tạo retargeter'), UNDECIDED: t('chưa có quyết định retarget') }
const slotStatus = (s: Slot) => s.approved ? t('Đã duyệt') : s.stale ? t('Timing đã đổi') : s.status === 'MISSING' ? t('Thiếu') : s.status === 'KIMODO' ? t('Chờ Kimodo') : s.low_confidence ? t('Độ tin cậy thấp') : t('Cần duyệt')
const blankReview: Review = { performance: false, camera: false, layout: false, keep: '', ai_change: '' }

export default function UeBranchPanel({ projectId, episodeId, onChanged }: { projectId: string; episodeId: string; onChanged: () => void }) {
  const [animation, setAnimation] = useState<Animation | null>(null)
  const [ue, setUe] = useState<UeState | null>(null)
  const [reviews, setReviews] = useState<Record<string, Review>>({})
  const [prompts, setPrompts] = useState<Record<string, string>>({})
  const [variants, setVariants] = useState<Record<string, string[]>>({})
  const [chosen, setChosen] = useState<Record<string, { path: string; tags: string }>>({})
  const [stopped, setStopped] = useState(false)
  const [exported, setExported] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(async () => {
    const [nextAnimation, nextUe] = await Promise.all([api<Animation>(`/episodes/${episodeId}/animation`), api<UeState>(`/episodes/${episodeId}/ue-branch`)])
    setAnimation(nextAnimation); setUe(nextUe)
  }, [episodeId])
  useEffect(() => { setReviews({}); setPrompts({}); setVariants({}); setChosen({}); setExported(''); setError(''); setNotice(''); load().catch(e => setError(errorLabel(e))) }, [load])
  const working = ue?.jobs.some(j => ['QUEUED', 'RUNNING'].includes(j.state)) ?? false
  useEffect(() => {
    if (!working) return
    const timer = setInterval(() => load().catch(e => setError(errorLabel(e))), 2500)
    return () => clearInterval(timer)
  }, [working, load])

  async function act(action: () => Promise<unknown>, message: string) {
    setBusy(true); setError(''); setNotice('')
    try { await action(); await load(); setNotice(message); onChanged() } catch (e) { setError(errorLabel(e)) } finally { setBusy(false) }
  }
  const assign = (slot: Slot, clips: { asset_path: string; blend_frames: number }[], upper: string | null) => api(`/episodes/${episodeId}/animation/slots/${slot.slot_id}/assign`, { expected_revision: slot.revision, clips, upper_body_path: upper })
  const plain = (slot: Slot) => slot.clips.map(c => ({ asset_path: c.asset_path, blend_frames: c.blend_frames }))

  const title = <div><h3>{t('Nhánh UE và nguồn Hybrid · 07, 08, C5')}</h3><p>{t('App tạo Master Sequence và shot sequence theo timing đã duyệt, gán animation từ thư viện, retarget hàng loạt rồi đặt vào track Body. Bạn chỉnh blocking, camera, VFX trong Unreal rồi chốt chuyển động từng shot.')}</p></div>
  if (!animation || !ue) return <section className="panel"><div className="panel-heading">{title}</div>{error && <div className="alert error" role="alert">{error}</div>}</section>
  const stuck = ue.jobs.find(j => j.state === 'RECONCILING')
  const review = (shot: UeShot) => reviews[shot.shot_id] ?? { ...blankReview, keep: shot.c5.notes?.keep ?? '', ai_change: shot.c5.notes?.ai_change ?? '' }

  return <section className="panel"><div className="panel-heading">{title}<span className={`status ${ue.summary.shots > 0 && ue.summary.locked === ue.summary.shots ? 'success' : 'neutral'}`}>{ue.summary.locked}/{ue.summary.shots}{t(' shot đã chốt C5')}</span></div>
    {error && <div className="alert error" role="alert">{error}</div>}
    {notice && <div className="alert notice" role="status">{notice}</div>}
    {!ue.ready && <div className="preflight blocked settings-result">{ue.blockers.join(' ')}{t(' Hoàn tất ở tab Tập phim trước khi dựng shot.')}</div>}
    {ue.ready && ue.shots.length === 0 && <p className="help">{t('Tập này không có shot UE hoặc Hybrid.')}</p>}
    {ue.ready && ue.shots.length > 0 && <>
      <h4>{t('07 · Tạo SQ và shot trong Unreal')}</h4>
      <div className="info-line"><span>Master: <code>{ue.master_path}</code></span><strong>{ue.summary.assembled}/{ue.summary.shots}{t(' shot có sequence đúng timing')}</strong></div>
      {ue.master_stale && <div className="preflight blocked settings-result">{t('Timing đã đổi nên vị trí các shot trong Master Sequence chưa khớp. Bấm nút dưới đây: shot đã khóa ở C5 giữ nguyên, chỉ shot đổi timing và Master được cập nhật.')}</div>}
      <div className="actions"><button className="button-primary" disabled={busy || working || Boolean(stuck) || !ue.unreal_configured} onClick={() => act(() => api(`/episodes/${episodeId}/ue-branch/assemble`, { shot_ids: null }), t('Đã xếp việc tạo sequence; Unreal chạy nền và tự cập nhật khi xong.'))}>{t('Tạo hoặc cập nhật sequence cho các shot chưa khóa')}</button></div>
      {!ue.unreal_configured && <div className="preflight blocked settings-result">{t('Chưa cấu hình Unreal trong Cài đặt.')}</div>}
      {stuck && <div className="preflight blocked settings-result"><strong>{t('Lần dựng trước bị gián đoạn.')}</strong>{t(' Đóng cửa sổ Unreal của lần dựng đó rồi xác nhận. ')}<label className="acceptance-confirm"><input type="checkbox" checked={stopped} onChange={e => setStopped(e.target.checked)} />{t('Tôi đã đóng Unreal của lần dựng cũ')}</label> <button disabled={busy || !stopped} onClick={() => act(async () => { await api(`/ue-branch/jobs/${stuck.id}/release`, { worker_stopped: true }); setStopped(false) }, t('Đã xác nhận; có thể dựng lại.'))}>{t('Xác nhận và cho dựng lại')}</button></div>}
      {ue.jobs.slice(0, 3).map(j => <div key={j.id} className="voice-job-list"><div><span className={`status ${j.state === 'COMPLETED' ? 'success' : ['BLOCKED', 'RECONCILING'].includes(j.state) ? 'warning' : 'neutral'}`}>{stateLabel(j.state)}</span> {j.kind === 'ASSEMBLE' ? t('Tạo sequence (07)') : t('Retarget và gắn Body (08-b6)')} · {j.shots.length} shot{j.error && <small className="row-error"> {j.error}</small>}{j.failed.map(f => <small key={f.shot_id} className="row-error"> {f.shot_id}: {f.error}</small>)}{j.retarget_notes.filter(r => r.status === 'FAILED').map(r => <small key={r.output_path} className="row-error"> Retarget {short(r.output_path)}: {r.error}</small>)}</div></div>)}

      <h4>{t('08-b1 → b3 · Gán animation')}</h4>
      <div className="info-line"><span>{animation.summary.slots}{t(' hành động · thư mục animation: ')}{animation.folders.join(', ') || t('chưa chọn ở CS1')}</span><strong>{t('Đã duyệt ')}{animation.summary.approved}/{animation.summary.slots}{animation.summary.missing ? t(' · thiếu {0}', animation.summary.missing) : ''}{animation.summary.kimodo ? t(' · chờ Kimodo {0}', animation.summary.kimodo) : ''}</strong></div>
      {animation.outdated && animation.slots.length > 0 && <div className="preflight blocked settings-result">{t('Danh sách hành động không còn khớp timing hoặc breakdown hiện tại. Chạy lại gán tự động; các gán đã duyệt mà hành động không đổi được giữ nguyên.')}</div>}
      <div className="actions"><button className={animation.slots.length ? '' : 'button-primary'} disabled={busy} onClick={() => act(() => api(`/episodes/${episodeId}/animation/propose`, {}), t('Đã tách hành động và gán animation từ thư viện.'))}>{animation.slots.length ? t('Chạy lại gán tự động (08-b1)') : t('Tự gán animation từ thư viện (08-b1)')}</button><button disabled={busy || !animation.slots.some(s => s.status === 'ASSIGNED' && !s.approved && !s.low_confidence && !s.stale && !s.issues.length)} onClick={() => act(() => api(`/episodes/${episodeId}/animation/approve-confident`, {}), t('Đã duyệt các gán không bị đánh dấu độ tin cậy thấp.'))}>{t('Duyệt mọi gán có độ tin cậy cao')}</button></div>
      {animation.slots.map(slot => <details key={slot.slot_id} className="scene-card" open={!slot.approved && (slot.status !== 'ASSIGNED' || slot.low_confidence || slot.issues.length > 0)}><summary><strong>{slot.shot_id} · {slot.character_name}</strong><span className="muted">{slot.tag}{slot.inferred_tag ? t(' (không có nhãn motion, mặc định đứng yên)') : ''} · frame {slot.start_frame}–{slot.end_frame} · {slot.clips.map(c => c.asset_name).join(' → ') || t('chưa có animation')}{slot.status === 'ASSIGNED' ? t(' · tin cậy {0}%', Math.round(slot.confidence * 100)) : ''}</span><span className={`status ${slot.approved ? 'success' : slot.status === 'MISSING' || slot.low_confidence || slot.issues.length || slot.stale ? 'warning' : 'neutral'}`}>{slotStatus(slot)}</span></summary>
        {slot.action && <p className="help">{t('Hành động trong breakdown: ')}{slot.action}</p>}
        {slot.clips.map((clip, index) => <div key={index} className="form-row"><div className="field grow"><span>Animation {slot.clips.length > 1 ? index + 1 : ''}</span><strong title={clip.asset_path}>{clip.asset_name} · {retargetLabel[clip.retarget ?? ''] ?? t('không còn trong lần quét')}</strong></div>{index > 0 && <label className="field">{t('Blend vào (frame)')}<input type="number" min={0} max={slot.frames} defaultValue={clip.blend_frames} onBlur={e => { const value = Math.max(0, Math.round(Number(e.target.value))); if (value !== clip.blend_frames) act(() => assign(slot, plain(slot).map((c, i) => i === index ? { ...c, blend_frames: value } : c), slot.upper_body_path), t('Đã đổi thời gian blend; gán này cần duyệt lại.')) }} /></label>}{slot.clips.length > 1 && <button className="button-quiet" disabled={busy} onClick={() => act(() => assign(slot, plain(slot).filter((_, i) => i !== index).map((c, i) => i === 0 ? { ...c, blend_frames: 0 } : c), slot.upper_body_path), t('Đã bỏ một animation khỏi chuỗi.'))}>{t('Bỏ')}</button>}</div>)}
        <AssetPicker projectId={projectId} categories="animation" label={slot.clips.length ? t('Thay bằng animation khác') : t('Chọn animation bù')} value={null} disabled={busy} clearable={false} onPick={path => path && act(() => assign(slot, [{ asset_path: path, blend_frames: 0 }], slot.upper_body_path), t('Đã chọn animation; gán này cần duyệt.'))} />
        {slot.clips.length > 0 && slot.clips.length < 4 && <AssetPicker projectId={projectId} categories="animation" label={t('Nối tiếp thêm animation (08-b3)')} value={null} disabled={busy} clearable={false} onPick={path => path && act(() => assign(slot, [...plain(slot), { asset_path: path, blend_frames: Math.min(6, slot.frames) }], slot.upper_body_path), t('Đã nối thêm animation với blend 6 frame.'))} />}
        {slot.clips.length > 0 && <AssetPicker projectId={projectId} categories="animation" label={t('Chồng lớp thân trên (08-b3, tùy chọn)')} value={slot.upper_body_path} disabled={busy} onPick={path => act(() => assign(slot, plain(slot), path), path ? t('Đã thêm animation thân trên; cần anim additive hoặc layered, kiểm tra trong Sequencer.') : t('Đã bỏ animation thân trên.'))} />}
        {slot.issues.map(i => <p key={i} className="row-error">{i}</p>)}
        <div className="actions"><button className="button-primary" disabled={busy || slot.approved || slot.status !== 'ASSIGNED' || slot.stale || slot.issues.length > 0} onClick={() => act(() => api(`/episodes/${episodeId}/animation/slots/${slot.slot_id}/approve`, { expected_revision: slot.revision }), t('Đã duyệt animation của {0}.', slot.slot_id))}>{t('Duyệt gán này (08-b2)')}</button>{slot.status !== 'KIMODO' && <button disabled={busy || slot.stale} onClick={() => act(() => api(`/episodes/${episodeId}/animation/slots/${slot.slot_id}/kimodo`, { expected_revision: slot.revision }), t('Đã chuyển hành động này sang danh sách tạo bằng Kimodo.'))}>{t('Chuyển sang Kimodo')}</button>}</div>
      </details>)}
      {animation.preview_note && animation.slots.length > 0 && <p className="help">{animation.preview_note}</p>}

      {animation.requests.length > 0 && <>
        <h4>{t('08-b4 / b5 · Motion thiếu → Kimodo')}</h4>
        <p className="help">{animation.kimodo_note}{t(' Sau khi quét lại asset, thư viện đổi nên cần duyệt lại C2 và chốt lại C3 ở tab Tập phim.')}</p>
        <div className="actions"><button disabled={busy || !animation.requests.some(r => r.state === 'PENDING')} onClick={() => act(async () => { const result = await api<{ relative_path: string }>(`/episodes/${episodeId}/animation/kimodo/export`, {}); setExported(result.relative_path) }, t('Đã xuất danh sách yêu cầu Kimodo vào thư mục dự án.'))}>{t('Xuất danh sách yêu cầu Kimodo')}</button>{exported && <span className="help">File: <code>{exported}</code></span>}</div>
        {animation.requests.map(request => { const picked = variants[request.id] ?? request.variant_paths; const choice = chosen[request.id] ?? { path: request.variant_paths[0] ?? '', tags: '' }; return <div key={request.id} className="route-shot">
          <div className="route-shot-head"><strong>{request.slot_id}</strong><span className="muted">{request.seconds}{t(' giây · ')}{request.variants}{t(' phương án')}</span><span className={`status ${request.state === 'APPROVED' ? 'success' : 'neutral'}`}>{request.state === 'APPROVED' ? t('Đã lưu vào thư viện') : request.state === 'RESULTS_ATTACHED' ? t('Chờ bạn chọn phương án') : t('Chờ tạo motion')}</span></div>
          {request.state === 'APPROVED' ? <p className="help">{t('Clip đã duyệt: ')}<code>{short(request.selected_path)}</code></p> : <>
            <label className="field">{t('Prompt Kimodo (tiếng Anh)')}<textarea rows={2} value={prompts[request.id] ?? request.prompt} onChange={e => setPrompts({ ...prompts, [request.id]: e.target.value })} /></label>
            <div className="actions"><button disabled={busy || (prompts[request.id] ?? request.prompt) === request.prompt} onClick={() => act(() => api(`/kimodo/requests/${request.id}/prompt`, { expected_revision: request.revision, prompt: prompts[request.id] }), t('Đã lưu prompt; yêu cầu quay lại trạng thái chờ tạo.'))}>{t('Lưu prompt (tạo lại motion)')}</button></div>
            {[0, 1, 2].map(index => <AssetPicker key={index} projectId={projectId} categories="animation" label={t('Phương án {0} đã import vào Unreal', index + 1)} value={picked[index] ?? null} disabled={busy} onPick={path => { const next = [...picked]; if (path) next[index] = path; else next.splice(index, 1); setVariants({ ...variants, [request.id]: next.filter(Boolean) }) }} />)}
            <div className="actions"><button disabled={busy || !picked.length || picked.join('|') === request.variant_paths.join('|')} onClick={() => act(async () => { await api(`/kimodo/requests/${request.id}/variants`, { expected_revision: request.revision, asset_paths: picked }); setVariants({ ...variants, [request.id]: [] }) }, t('Đã gắn các phương án; chọn một phương án để duyệt.'))}>{t('Gắn phương án')}</button></div>
            {request.state === 'RESULTS_ATTACHED' && <div className="form-row"><label className="field">{t('Phương án chọn')}<select value={choice.path} onChange={e => setChosen({ ...chosen, [request.id]: { ...choice, path: e.target.value } })}>{request.variant_paths.map(p => <option key={p} value={p}>{short(p)}</option>)}</select></label><label className="field grow">{t('Nhãn để các tập sau tìm lại (tiếng Anh, cách nhau bằng dấu phẩy)')}<input value={choice.tags} onChange={e => setChosen({ ...chosen, [request.id]: { ...choice, tags: e.target.value } })} placeholder="swim, breaststroke" /></label><button className="button-primary" disabled={busy || !choice.path || !choice.tags.trim()} onClick={() => act(() => api(`/kimodo/requests/${request.id}/approve`, { expected_revision: request.revision, asset_path: choice.path, tags: choice.tags.split(',').map(entry => entry.trim().toLowerCase()).filter(Boolean) }), t('Đã duyệt motion và lưu vào thư viện series.'))}>{t('Duyệt và lưu vào thư viện (08-b5)')}</button></div>}
          </>}
        </div> })}
      </>}

      <h4>{t('08-b6 · Retarget hàng loạt và gắn vào Body')}</h4>
      <div className="actions"><button className="button-primary" disabled={busy || working || Boolean(stuck) || !ue.unreal_configured || animation.outdated || !ue.shots.some(s => !s.c5.locked && s.assembled === 'CURRENT' && s.slots_total > 0 && s.slots_pending === 0 && s.animated !== 'CURRENT')} onClick={() => act(() => api(`/episodes/${episodeId}/ue-branch/animate`, { shot_ids: null }), t('Đã xếp việc retarget và gắn Body cho các shot sẵn sàng.'))}>{t('Retarget và gắn animation vào Body')}</button></div>
      <p className="help">{t('Chạy cho các shot đã có sequence đúng timing và mọi hành động đã duyệt animation. Animation cùng skeleton được gắn thẳng; skeleton khác dùng retargeter đã duyệt ở CS1 hoặc app tạo mới (cần bạn kiểm tra pose). Kết quả lưu trong thư mục của tập; anim đã retarget được dùng lại.')}</p>

      <h4>{t('C5 · Review và chốt chuyển động')}</h4>
      {ue.manual_note && <p className="help">{ue.manual_note}</p>}
      {ue.shots.map(shot => { const r = review(shot); const set = (change: Partial<Review>) => setReviews({ ...reviews, [shot.shot_id]: { ...r, ...change } }); const ready = shot.assembled === 'CURRENT' && ['CURRENT', 'NOT_NEEDED'].includes(shot.animated); return <details key={shot.shot_id} className="scene-card"><summary><strong>{shot.shot_id} · {shot.route === 'HYBRID' ? t('nguồn UE của Hybrid') : 'UE'}</strong><span className="muted">{shot.length_frames} frame · {shot.characters.join(', ') || t('không nhân vật')} · sequence: {buildLabel[shot.assembled]} · animation: {buildLabel[shot.animated]}</span><span className={`status ${shot.c5.locked ? 'success' : shot.c5.stale || shot.issues.length ? 'warning' : 'neutral'}`}>{shot.c5.locked ? t('Đã chốt C5') : shot.c5.stale ? t('Đã đổi sau khi chốt') : ready ? t('Chờ bạn xem trong Unreal') : t('Chưa dựng xong')}</span></summary>
        <p className="help">{shot.description}</p>
        <p className="help">Sequence: <code>{shot.sequence_path}</code> · level: <code>{short(shot.map_path)}</code>{shot.camera_note ? t(' · ghi chú máy quay: {0}', shot.camera_note) : ''}{shot.vfx_tags.length ? t(' · VFX cần đặt: {0}', shot.vfx_tags.join(', ')) : ''}{shot.lines ? t(' · {0} câu thoại đã đặt audio', shot.lines) : ''}</p>
        {shot.issues.map(i => <p key={i} className="row-error">{i}</p>)}
        {shot.build_notes.map(n => <p key={n} className={n.startsWith(t('Lỗi')) ? 'row-error' : 'help'}>Unreal: {n}</p>)}
        {shot.c5.locked ? <div className="actions"><button disabled={busy} onClick={() => act(() => api(`/episodes/${episodeId}/ue-branch/shots/${shot.shot_id}/c5/unlock`, {}), t('Đã mở khóa {0}.', shot.shot_id))}>{t('Mở khóa để sửa')}</button></div> : <>
          <div className="actions"><label className="acceptance-confirm"><input type="checkbox" checked={r.performance} onChange={e => set({ performance: e.target.checked })} />{t('Đã xem diễn xuất và lip-sync')}</label><label className="acceptance-confirm"><input type="checkbox" checked={r.camera} onChange={e => set({ camera: e.target.checked })} />{t('Đã đặt và xem camera')}</label><label className="acceptance-confirm"><input type="checkbox" checked={r.layout} onChange={e => set({ layout: e.target.checked })} />{t('Đã xem bố cục, blocking và VFX')}</label></div>
          {shot.route === 'HYBRID' && <div className="planner-fields"><label className="field">{t('Phần giữ nguyên từ UE')}<textarea rows={2} maxLength={400} value={r.keep} onChange={e => set({ keep: e.target.value })} placeholder={t('Ví dụ: bố cục, chuyển động nhân vật, góc máy')} /></label><label className="field">{t('Phần để AI sửa')}<textarea rows={2} maxLength={400} value={r.ai_change} onChange={e => set({ ai_change: e.target.value })} placeholder={t('Ví dụ: thêm mưa và sét, chất liệu nước')} /></label></div>}
          <div className="actions"><button className="button-primary" disabled={busy || !ready || !r.performance || !r.camera || !r.layout || (shot.route === 'HYBRID' && (!r.keep.trim() || !r.ai_change.trim()))} onClick={() => act(async () => { await api(`/episodes/${episodeId}/ue-branch/shots/${shot.shot_id}/c5`, { expected_hash: shot.build_hash, checks: { performance: true, camera: true, layout: true }, notes: shot.route === 'HYBRID' ? { keep: r.keep, ai_change: r.ai_change } : {} }); setReviews(Object.fromEntries(Object.entries(reviews).filter(([k]) => k !== shot.shot_id))) }, t('Đã chốt chuyển động {0} (C5).', shot.shot_id))}>{shot.route === 'HYBRID' ? t('Chốt nguồn UE (C5)') : t('Chốt chuyển động (C5)')}</button></div>
        </>}
      </details> })}
      <p className="help">{t('C5 ghi nhận bạn đã xem shot trong Unreal; app không tự đánh giá hình ảnh. Shot đã chốt không bị app dựng lại. Bước tiếp theo là cache và proxy (09/C6) ngay bên dưới.')}</p>
    </>}
  </section>
}
