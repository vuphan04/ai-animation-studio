import { useEffect, useState } from 'react'
import { api, type Asset, type Episode, type Shot } from './api'
import { errorLabel, stateLabel } from './uiText'

type Picked = { relative_path: string | null; sha256: string | null }
type RefSide = { shot_id: string; source_take_id: string; frame: number; relative_path: string; sha256: string }
type RefInput = { previous: RefSide | null; next: RefSide | null; character_asset_version_ids: string[]; environment_asset_version_id: string; style_asset_version_id: string; control_video_take_id: string | null }
type Capability = { provider_kind: string; model: string; operational: boolean; currency: string; presets: Record<string, string>; source: string; note: string }
type Gate = { allowed: boolean; blockers: string[]; route?: string; selected_final_take_id?: string | null; picture_lock_id?: string | null }
type Job = { id: string; shot_id: string; stage?: string; state: string; operation_id: string; provider_request_id?: string | null; estimated_micro?: number; actual_micro?: number | null; error?: string | null }
type Take = { id: string; shot_id: string; stage: string; route: string; version: number; relative_path: string; sha256: string; review: string; validity: string; provenance: string; provider_request_id: string | null }
type Lock = { id: string; version: number; validity: string; shot_revision: number; final_take_id: string }
type Stem = { id: string; role: string; version: number; relative_path: string; review: string; validity: string; provenance: string; offset_frame: number }
type Grant = { id: string; currency: string; max_micro: number; reserved_micro: number; spent_micro: number; expires_at: string }
type Package = { id: string; version: number; state: string; relative_path: string; format: string }
type DeliveryProfile = { revision: number; width: number; height: number; color_profile: string; audio_sample_rate: number; timecode_start: string }

const roles = ['Dialogue', 'Foley', 'SFX', 'Ambience', 'Music']
const checks = ['identity', 'costume', 'camera', 'timing', 'lip_sync'] as const
const checkNames: Record<(typeof checks)[number], string> = {
  identity: 'Nhân vật', costume: 'Trang phục', camera: 'Máy quay', timing: 'Nhịp hình', lip_sync: 'Khớp thoại'
}

export default function MediaPage({ projectId, episodes, shots, assets }: { projectId: string; episodes: Episode[]; shots: Shot[]; assets: Asset[] }) {
  const [shotId, setShotId] = useState('')
  const [episodeId, setEpisodeId] = useState('')
  const [capability, setCapability] = useState<Capability | null>(null)
  const [grants, setGrants] = useState<Grant[]>([])
  const [grantId, setGrantId] = useState('')
  const [grantAmount, setGrantAmount] = useState('10')
  const [grantExpiry, setGrantExpiry] = useState('')
  const [stage, setStage] = useState<'DRAFT' | 'FINAL'>('DRAFT')
  const [videoGate, setVideoGate] = useState<Gate | null>(null)
  const [pictureGate, setPictureGate] = useState<Gate | null>(null)
  const [foleyGate, setFoleyGate] = useState<Gate | null>(null)
  const [deliveryGate, setDeliveryGate] = useState<Gate | null>(null)
  const [videoJobs, setVideoJobs] = useState<Job[]>([])
  const [foleyJobs, setFoleyJobs] = useState<Job[]>([])
  const [takes, setTakes] = useState<Take[]>([])
  const [locks, setLocks] = useState<Lock[]>([])
  const [neighborTakes, setNeighborTakes] = useState<{ previous: Take[]; next: Take[] }>({ previous: [], next: [] })
  const [route, setRoute] = useState('UNREAL')
  const [approvedRoute, setApprovedRoute] = useState('')
  const [assetRefId, setAssetRefId] = useState('')
  const [assetRefFile, setAssetRefFile] = useState<Picked>({ relative_path: null, sha256: null })
  const [assetRefs, setAssetRefs] = useState<{ id: string; asset_version_id: string; version: number; relative_path: string }[]>([])
  const [profile, setProfile] = useState<DeliveryProfile>({ revision: 0, width: 1280, height: 720, color_profile: '', audio_sample_rate: 48000, timecode_start: '01:00:00:00' })
  const [stems, setStems] = useState<Stem[]>([])
  const [packages, setPackages] = useState<Package[]>([])
  const [references, setReferences] = useState<{ id: string; version: number; validity: string }[]>([])
  const [refForm, setRefForm] = useState<RefInput>({ previous: null, next: null, character_asset_version_ids: [], environment_asset_version_id: '', style_asset_version_id: '', control_video_take_id: null })
  const [stemRole, setStemRole] = useState('Foley')
  const [stemFile, setStemFile] = useState<Picked>({ relative_path: null, sha256: null })
  const [stemOffset, setStemOffset] = useState(0)
  const [reviewChecks, setReviewChecks] = useState<Record<string, boolean>>({})
  const [previewTakeId, setPreviewTakeId] = useState('')
  const [fallbackTakeId, setFallbackTakeId] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const activeVideo = videoJobs.some(j => ['QUEUED', 'RUNNING', 'PENDING'].includes(j.state))
  const activeFoley = foleyJobs.some(j => ['QUEUED', 'RUNNING'].includes(j.state))
  const activeDelivery = packages.some(p => ['QUEUED', 'BUILDING'].includes(p.state))

  const shot = shots.find(item => item.id === shotId)
  const adjacent = (side: 'previous' | 'next') => shots.find(item => item.episode_id === shot?.episode_id && item.ordinal === (shot?.ordinal ?? 0) + (side === 'previous' ? -1 : 1))

  async function load() {
    if (!projectId) return
    const [cap, gs, jobs, fj, ar] = await Promise.all([
      api<Capability>('/media/video/capability'), api<Grant[]>(`/projects/${projectId}/media/video/grants`),
      api<Job[]>(`/projects/${projectId}/media/video/jobs`), api<Job[]>(`/projects/${projectId}/media/foley/jobs`), api<typeof assetRefs>(`/projects/${projectId}/media/asset-references`)
    ])
    setCapability(cap); setGrants(gs); setVideoJobs(jobs); setFoleyJobs(fj); setAssetRefs(ar)
    if (shotId) {
      const prev = adjacent('previous'), next = adjacent('next')
      const [takeRows, lockRows, stemRows, referenceRows, vg, pg, fg, prevTakes, nextTakes] = await Promise.all([
        api<Take[]>(`/shots/${shotId}/media/video/takes`), api<Lock[]>(`/shots/${shotId}/media/picture/locks`),
        api<Stem[]>(`/shots/${shotId}/media/stems`), api<{ id: string; version: number; validity: string }[]>(`/shots/${shotId}/media/references`),
        api<Gate>(`/shots/${shotId}/media/video/preflight?stage=${stage}${grantId ? `&grant_id=${encodeURIComponent(grantId)}` : ''}`),
        api<Gate>(`/shots/${shotId}/media/picture/preflight`), api<Gate>(`/shots/${shotId}/media/foley/preflight`),
        prev ? api<Take[]>(`/shots/${prev.id}/media/video/takes`) : Promise.resolve([]),
        next ? api<Take[]>(`/shots/${next.id}/media/video/takes`) : Promise.resolve([])
      ])
      setTakes(takeRows); setLocks(lockRows); setStems(stemRows); setReferences(referenceRows)
      setVideoGate(vg); setPictureGate(pg); setFoleyGate(fg); setNeighborTakes({ previous: prevTakes, next: nextTakes }); setApprovedRoute(vg.route ?? '')
    }
    if (episodeId) {
      const [gate, rows, savedProfile] = await Promise.all([
        api<Gate>(`/episodes/${episodeId}/media/delivery/preflight`),
        api<Package[]>(`/episodes/${episodeId}/media/delivery/packages`), api<DeliveryProfile>(`/episodes/${episodeId}/media/delivery/profile`)
      ])
      setDeliveryGate(gate); setPackages(rows); setProfile(savedProfile)
    }
  }

  useEffect(() => { load().catch(e => setError(errorLabel(e))) }, [projectId, shotId, episodeId, stage, grantId, shots.length])
  useEffect(() => { setShotId(''); setEpisodeId(''); setGrantId(''); setRefForm({ previous: null, next: null, character_asset_version_ids: [], environment_asset_version_id: '', style_asset_version_id: '', control_video_take_id: null }) }, [projectId])
  useEffect(() => { setRefForm({ previous: null, next: null, character_asset_version_ids: [], environment_asset_version_id: '', style_asset_version_id: '', control_video_take_id: null }); setReviewChecks({}) }, [shotId])
  useEffect(() => {
    if (!projectId || (!activeVideo && !activeFoley && !activeDelivery)) return
    const timer = window.setInterval(() => {
      Promise.all([api<Job[]>(`/projects/${projectId}/media/video/jobs`), api<Job[]>(`/projects/${projectId}/media/foley/jobs`)]).then(([vj, fj]) => { setVideoJobs(vj); setFoleyJobs(fj) }).catch(() => {})
      if (episodeId && activeDelivery) api<Package[]>(`/episodes/${episodeId}/media/delivery/packages`).then(setPackages).catch(() => {})
      if (shotId) {
        api<Take[]>(`/shots/${shotId}/media/video/takes`).then(setTakes).catch(() => {})
        api<Stem[]>(`/shots/${shotId}/media/stems`).then(setStems).catch(() => {})
      }
    }, 3000)
    return () => window.clearInterval(timer)
  }, [projectId, episodeId, shotId, activeVideo, activeFoley, activeDelivery])

  async function act(work: () => Promise<unknown>, success: string) {
    setError(''); setNotice(''); setBusy(true)
    try { await work(); await load(); setNotice(success) } catch (e) { setError(errorLabel(e)) } finally { setBusy(false) }
  }

  async function pick(kind: 'reference_image' | 'audio_stem' | 'video_file'): Promise<Picked> {
    return api<Picked>(`/projects/${projectId}/outputs/browse`, { kind })
  }

  function sideField(side: 'previous' | 'next') {
    const neighbor = adjacent(side), takesForSide = neighborTakes[side].filter(item => item.validity === 'CURRENT' && item.review === 'APPROVED' && item.stage === 'FINAL')
    const current = refForm[side]
    return <div className="reference-side" key={side}>
      <strong>{side === 'previous' ? 'Khung cuối cảnh trước' : 'Khung đầu cảnh sau'}</strong>
      <p className="help">{neighbor ? `${neighbor.code} · ${side === 'previous' ? neighbor.manifest.timeline.out_exclusive - 1 : neighbor.manifest.timeline.in} frame` : 'Đầu/cuối tập: không cần cảnh liền kề.'}</p>
      {neighbor && <><label className="field">Final take đã duyệt của cảnh liền kề
        <select value={current?.source_take_id ?? ''} onChange={e => setRefForm(old => ({ ...old, [side]: e.target.value ? { shot_id: neighbor.id, source_take_id: e.target.value, frame: side === 'previous' ? neighbor.manifest.timeline.out_exclusive - 1 : neighbor.manifest.timeline.in, relative_path: current?.relative_path ?? '', sha256: current?.sha256 ?? '' } : null }))}>
          <option value="">Chọn final take</option>{takesForSide.map(take => <option key={take.id} value={take.id}>Bản {take.version} · {take.route}</option>)}
        </select>
      </label><button disabled={busy} onClick={() => act(async () => {
        const file = await pick('reference_image')
        if (file.relative_path && file.sha256) setRefForm(old => ({ ...old, [side]: { shot_id: neighbor.id, source_take_id: old[side]?.source_take_id ?? '', frame: side === 'previous' ? neighbor.manifest.timeline.out_exclusive - 1 : neighbor.manifest.timeline.in, relative_path: file.relative_path!, sha256: file.sha256! } }))
      }, 'Đã chọn ảnh tham chiếu.')}>Duyệt ảnh</button><small className="muted">{current?.relative_path || 'Chưa chọn ảnh'}</small></>}
    </div>
  }

  if (!projectId) return <section className="panel"><p>Chọn dự án để quản lý video và bàn giao.</p></section>

  return <div className="stack media-page">
    <div className="simulation-note"><span className="mode-dot" /><p><strong>P6 đang chờ kiểm chứng trên dịch vụ/UE/Resolve thật.</strong> Capability trong tài liệu không phải kết quả gọi API. Chưa có key, giá, UE final receipt hoặc Foley runtime; nút có phí và Picture Lock sẽ báo chặn.</p></div>
    {error && <div className="alert error" role="alert">{error}</div>}{notice && <div className="alert notice" role="status">{notice}</div>}
    <div className="form-row"><label className="field grow">Cảnh quay<select value={shotId} onChange={e => { setShotId(e.target.value); setVideoGate(null); setPictureGate(null) }}><option value="">Chọn cảnh</option>{shots.map(item => <option key={item.id} value={item.id}>{item.code} · {item.approval?.decision.approved_route ?? 'chưa duyệt route'}</option>)}</select></label><label className="field grow">Tập phim<select value={episodeId} onChange={e => setEpisodeId(e.target.value)}><option value="">Chọn tập bàn giao</option>{episodes.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><button onClick={() => load().catch(e => setError(errorLabel(e)))}>Tải lại trạng thái</button></div>

    <section className="panel"><div className="panel-heading"><div><h3>Nhà cung cấp video & ngân sách</h3><p>Seedance chỉ hoạt động sau khi kiểm chứng account, upload, giá và đối soát request.</p></div><span className="status warning">{capability?.operational ? 'CÓ CẤU HÌNH' : 'CHƯA MỞ LIVE'}</span></div>
      <p>{capability?.model ?? 'Đang tải'} · nháp {capability?.presets.DRAFT ?? '480p'} · final {capability?.presets.FINAL ?? '720p'} · tiền tệ {capability?.currency ?? 'CNY'}</p>
      <div className="planner-fields"><label className="field">Giới hạn ngân sách ({capability?.currency ?? 'CNY'})<input type="number" min="0.01" step="0.01" value={grantAmount} onChange={e => setGrantAmount(e.target.value)} /></label><label className="field">Hết hạn grant<input type="datetime-local" value={grantExpiry} onChange={e => setGrantExpiry(e.target.value)} /></label><label className="field">Grant dùng cho request<select value={grantId} onChange={e => setGrantId(e.target.value)}><option value="">Chưa chọn</option>{grants.map(grant => <option key={grant.id} value={grant.id}>{(grant.max_micro / 1_000_000).toFixed(2)} {grant.currency} · còn {((grant.max_micro - grant.reserved_micro - grant.spent_micro) / 1_000_000).toFixed(2)}</option>)}</select></label></div>
      <div className="actions"><button disabled={busy || !grantExpiry || Number(grantAmount) <= 0} onClick={() => act(async () => { await api(`/projects/${projectId}/media/video/grants`, { currency: capability?.currency ?? 'CNY', max_micro: Math.round(Number(grantAmount) * 1_000_000), expires_at: new Date(grantExpiry).toISOString() }) }, 'Đã lưu hạn mức video. Chưa gửi request có phí.')}>Lưu hạn mức</button></div>
      <p className="help">Grant chỉ đặt trần chi tiêu; mỗi lần tạo video còn cần route được duyệt và người dùng bấm chạy. Trạng thái tài liệu: <a href={capability?.source} target="_blank" rel="noreferrer">API provider</a>.</p>
    </section>

    {shot && <>
      <section className="panel"><div className="panel-heading"><div><h3>C5 · Tuyến sản xuất</h3><p>Tuyến hiện hành: {approvedRoute || 'chưa duyệt C1'}. Thay tuyến làm Picture Lock và âm thanh cũ cần cập nhật.</p></div></div>
        <div className="form-row"><label className="field grow">Tuyến cần duyệt<select value={route} onChange={e => setRoute(e.target.value)}><option value="UNREAL">Unreal</option><option value="SEEDANCE">AI video · Seedance</option><option value="HYBRID">Kết hợp UE control video + AI</option></select></label><button disabled={busy || !shot.approval} onClick={() => act(() => api(`/shots/${shot.id}/media/route`, { expected_shot_revision: shot.current_revision, route }), 'Đã duyệt tuyến C5 đúng phiên bản.')}>Duyệt tuyến C5</button>
          <button disabled={busy} onClick={() => act(async () => { const file = await pick('video_file'); if (file.relative_path && file.sha256) await api(`/shots/${shot.id}/media/ue-video`, { expected_shot_revision: shot.current_revision, relative_path: file.relative_path, sha256: file.sha256, duration_frames: shot.manifest.timeline.out_exclusive - shot.manifest.timeline.in, fps: shot.manifest.fps }) }, 'Đã nhập file UE để review. FPS/duration đang là khai báo theo manifest; chưa có bằng chứng MRQ.')}>Nhập video UE để review</button></div>
        <p className="help">File UE nhập thủ công chưa xác minh FPS, thời lượng hoặc receipt MRQ; không đủ để khóa hình hay làm control video HYBRID.</p>
        <div className="form-row"><label className="field grow">UE fallback khi AI take bị drift<select value={fallbackTakeId} onChange={e => setFallbackTakeId(e.target.value)}><option value="">Chọn UE final đã review</option>{takes.filter(t => t.route === 'UNREAL' && t.stage === 'FINAL' && t.review === 'APPROVED' && t.validity === 'CURRENT').map(t => <option key={t.id} value={t.id}>v{t.version} · {t.relative_path}</option>)}</select></label><button disabled={busy || !fallbackTakeId} onClick={() => act(async () => { const take = takes.find(t => t.id === fallbackTakeId)!; await api(`/shots/${shot.id}/media/route`, { expected_shot_revision: shot.current_revision, route: 'UNREAL', fallback_take_id: take.id }); await api(`/media/video/takes/${take.id}/select`, { expected_version: take.version }) }, 'Đã duyệt tuyến UE fallback và chọn take; Dialogue canon giữ nguyên.')}>Duyệt & chọn UE fallback</button></div>
      </section>
      <section className="panel"><div className="panel-heading"><div><h3>Ảnh nhân vật, bối cảnh và phong cách</h3><p>Đăng ký ảnh PNG/JPEG trong thư mục dự án, gắn đúng phiên bản tài nguyên và duyệt nội dung.</p></div></div>
        <div className="form-row"><label className="field grow">Phiên bản tài nguyên<select value={assetRefId} onChange={e => setAssetRefId(e.target.value)}><option value="">Chọn tài nguyên</option>{assets.filter(a => ['character', 'environment', 'style_reference'].includes(a.kind)).map(a => <option key={a.id} value={a.id}>{a.registry_id} v{a.version}</option>)}</select></label><button disabled={busy} onClick={() => act(async () => { setAssetRefFile(await pick('reference_image')) }, 'Đã chọn ảnh; kiểm tra đúng tài nguyên trước khi duyệt.')}>Duyệt ảnh</button><button disabled={busy || !assetRefId || !assetRefFile.relative_path} onClick={() => act(() => api(`/projects/${projectId}/media/asset-references`, { asset_version_id: assetRefId, relative_path: assetRefFile.relative_path, sha256: assetRefFile.sha256 }), 'Đã duyệt ảnh đúng phiên bản tài nguyên.')}>Duyệt nội dung & lưu</button></div>
        <p className="help">{assetRefFile.relative_path || 'Chưa chọn ảnh.'} · {assetRefs.length} ảnh đã đăng ký.</p>
      </section>
      <section className="panel"><div className="panel-heading"><div><h3>Gói tham chiếu cảnh</h3><p>Duyệt ảnh từ final take của cảnh liền kề và phiên bản nhân vật, bối cảnh, phong cách từ Thư viện.</p></div><span className="count">{references.length} bản</span></div>
        <div className="planner-fields"><label className="field">Nhân vật (giữ Ctrl để chọn nhiều)<select multiple value={refForm.character_asset_version_ids} onChange={e => setRefForm(old => ({ ...old, character_asset_version_ids: Array.from(e.target.selectedOptions, option => option.value) }))}>{assets.filter(item => item.kind === 'character').map(item => <option key={item.id} value={item.id}>{item.registry_id} v{item.version}</option>)}</select></label><label className="field">Bối cảnh<select value={refForm.environment_asset_version_id} onChange={e => setRefForm(old => ({ ...old, environment_asset_version_id: e.target.value }))}><option value="">Chọn bối cảnh</option>{assets.filter(item => item.kind === 'environment').map(item => <option key={item.id} value={item.id}>{item.registry_id} v{item.version}</option>)}</select></label><label className="field">Phong cách<select value={refForm.style_asset_version_id} onChange={e => setRefForm(old => ({ ...old, style_asset_version_id: e.target.value }))}><option value="">Chọn phong cách</option>{assets.filter(item => item.kind === 'style_reference').map(item => <option key={item.id} value={item.id}>{item.registry_id} v{item.version}</option>)}</select></label></div>
        <div className="reference-grid">{sideField('previous')}{sideField('next')}</div>
        {approvedRoute === 'HYBRID' && <label className="field">UE control video đã duyệt<select value={refForm.control_video_take_id ?? ''} onChange={e => setRefForm(old => ({ ...old, control_video_take_id: e.target.value || null }))}><option value="">Chọn UE final take</option>{takes.filter(t => t.route === 'UNREAL' && t.stage === 'FINAL' && t.review === 'APPROVED' && t.validity === 'CURRENT').map(t => <option key={t.id} value={t.id}>v{t.version} · {t.provenance}</option>)}</select></label>}
        <div className="actions"><button disabled={busy} onClick={() => act(() => api(`/shots/${shot.id}/media/references`, { expected_shot_revision: shot.current_revision, references: refForm }), 'Đã tạo reference package phiên bản mới.')}>Kiểm tra & lưu gói</button></div>
        {references.map(ref => <p key={ref.id} className="help">Bản {ref.version} · {stateLabel(ref.validity)} · {ref.id}</p>)}
      </section>

      <section className="panel"><div className="panel-heading"><div><h3>Nháp và final video</h3><p>Final là request/output mới từ draft được chọn. Âm thanh AI video không thay Dialogue canon.</p></div><span className="status warning">{videoGate?.allowed ? 'SẴN SÀNG' : 'BỊ CHẶN'}</span></div>
        <label className="field">Xem take trước khi đánh giá<select value={previewTakeId} onChange={e => setPreviewTakeId(e.target.value)}><option value="">Chọn video</option>{takes.map(t => <option key={t.id} value={t.id}>{t.stage === 'DRAFT' ? 'Nháp' : 'Final'} v{t.version} · {t.route}</option>)}</select></label>
        {previewTakeId && takes.some(t => t.id === previewTakeId) && <video key={previewTakeId} className="media-preview" src={`/api/media/video/takes/${encodeURIComponent(previewTakeId)}/file`} controls preload="metadata" />}
        <div className="form-row"><label className="field">Giai đoạn<select value={stage} onChange={e => setStage(e.target.value as 'DRAFT' | 'FINAL')}><option value="DRAFT">Nháp 480p</option><option value="FINAL">Final 720p</option></select></label><button disabled={busy} onClick={() => act(async () => { await api(`/shots/${shot.id}/media/video/jobs`, { stage, grant_id: grantId, client_request_id: crypto.randomUUID() }) }, 'Đã xếp video job; xem trạng thái bên dưới.')}>Tạo {stage === 'DRAFT' ? 'nháp' : 'final'}</button></div>
        {videoGate?.blockers.map((item, i) => <p className="help" key={i}>• {item}</p>)}
        <div className="table-wrap"><table><thead><tr><th>Take</th><th>File / nguồn</th><th>Đánh giá</th><th>Thao tác</th></tr></thead><tbody>{takes.map(take => <tr key={take.id}><td>{take.stage} v{take.version}<br /><small>{stateLabel(take.validity)}</small></td><td><code>{take.relative_path}</code><br /><small>{take.provenance}</small></td><td>{stateLabel(take.review)}{take.review === 'NEEDS_REVIEW' && <div className="review-checks">{checks.map(key => <label key={key}><input type="checkbox" checked={reviewChecks[`${take.id}:${key}`] ?? false} onChange={e => setReviewChecks(old => ({ ...old, [`${take.id}:${key}`]: e.target.checked }))} />{checkNames[key]}</label>)}</div>}</td><td className="table-actions">{take.review === 'NEEDS_REVIEW' && <><button disabled={busy} onClick={() => act(() => api(`/media/video/takes/${take.id}/review`, { expected_version: take.version, decision: 'APPROVED', checks: Object.fromEntries(checks.map(key => [key, reviewChecks[`${take.id}:${key}`] ?? false])) }), 'Đã duyệt take.')}>Duyệt</button><button disabled={busy} onClick={() => act(() => api(`/media/video/takes/${take.id}/review`, { expected_version: take.version, decision: 'REJECTED', checks: Object.fromEntries(checks.map(key => [key, reviewChecks[`${take.id}:${key}`] ?? false])) }), 'Đã từ chối take drift.')}>Từ chối</button></>}{take.review === 'APPROVED' && <button disabled={busy} onClick={() => act(() => api(`/media/video/takes/${take.id}/select`, { expected_version: take.version }), 'Đã chọn take. Picture Lock và sound cũ sẽ STALE khi đổi final.')}>Chọn take</button>}</td></tr>)}</tbody></table></div>
        <h4>Video jobs</h4>{videoJobs.filter(job => job.shot_id === shot.id).map(job => <div className="review-row" key={job.id}><div><strong>{job.stage} · {stateLabel(job.state)}</strong><small>Operation {job.operation_id.slice(0, 12)} · Provider ID {job.provider_request_id || 'chưa có'}</small>{job.error && <small>{job.error}</small>}</div>{['PENDING', 'RECONCILING'].includes(job.state) && <button disabled={busy || !job.provider_request_id} onClick={() => act(() => api(`/media/video/jobs/${job.id}/reconcile`, {}), 'Đã hỏi lại provider theo task ID.')}>Đối soát</button>}</div>)}
      </section>

      <section className="panel"><div className="panel-heading"><div><h3>C6 · Picture Lock</h3><p>Chỉ khóa khi selected final take và edit timing được duyệt đúng phiên bản.</p></div><span className="status warning">{pictureGate?.allowed ? 'SẴN SÀNG' : 'BỊ CHẶN'}</span></div>
        {pictureGate?.blockers.map((item, i) => <p className="help" key={i}>• {item}</p>)}
        <div className="actions"><button disabled={busy || !pictureGate?.allowed || !pictureGate.selected_final_take_id} onClick={() => act(() => api(`/shots/${shot.id}/media/picture/lock`, { expected_shot_revision: shot.current_revision, expected_take_id: pictureGate?.selected_final_take_id, edit_in: shot.manifest.timeline.in, edit_out_exclusive: shot.manifest.timeline.out_exclusive }), 'Đã khóa picture exact version.')}>Khóa Picture</button></div>
        {locks.map(lock => <p key={lock.id} className="help">Picture v{lock.version} · {stateLabel(lock.validity)} · revision {lock.shot_revision}</p>)}
      </section>

      <section className="panel"><div className="panel-heading"><div><h3>C7 · Foley và stem</h3><p>Dialogue dùng WAV canon đã khóa. Mỗi loại âm thanh là một stem riêng.</p></div><span className="status warning">{foleyGate?.allowed ? 'SẴN SÀNG' : 'BỊ CHẶN'}</span></div>
        {foleyGate?.blockers.map((item, i) => <p className="help" key={i}>• {item}</p>)}
        <button disabled={busy || !foleyGate?.allowed} onClick={() => act(() => api(`/shots/${shot.id}/media/foley/jobs`, { client_request_id: crypto.randomUUID() }), 'Đã xếp Foley job.')}>Tạo Foley từ Picture Lock</button>
        <div className="planner-fields"><label className="field">Loại stem<select value={stemRole} onChange={e => setStemRole(e.target.value)}>{roles.map(role => <option key={role} value={role}>{role}</option>)}</select></label><label className="field">File WAV<input value={stemFile.relative_path ?? ''} readOnly placeholder="Chọn bằng nút Duyệt" /></label><label className="field">Offset (frame)<input type="number" min="0" value={stemOffset} onChange={e => setStemOffset(Number(e.target.value))} /></label></div>
        <div className="actions"><button disabled={busy} onClick={() => act(async () => { const file = await pick('audio_stem'); if (file.relative_path) setStemFile(file) }, 'Đã chọn WAV trong project.')}>Duyệt WAV</button><button disabled={busy || !stemFile.relative_path || !stemFile.sha256 || !locks.some(lock => lock.validity === 'CURRENT')} onClick={() => act(() => api(`/shots/${shot.id}/media/stems`, { picture_lock_id: locks.find(lock => lock.validity === 'CURRENT')?.id, role: stemRole, relative_path: stemFile.relative_path, sha256: stemFile.sha256, offset_frame: stemOffset }), 'Đã đăng ký stem; cần nghe và duyệt riêng.')}>Đăng ký stem</button></div>
        {stems.map(stem => <div className="review-row" key={stem.id}><div><strong>{stem.role} v{stem.version} · {stateLabel(stem.review)} · {stateLabel(stem.validity)}</strong><small>{stem.relative_path} · offset {stem.offset_frame} frame · {stem.provenance}</small></div>{stem.review === 'NEEDS_REVIEW' && <button disabled={busy} onClick={() => act(() => api(`/media/stems/${stem.id}/review`, { expected_version: stem.version }), 'Đã duyệt stem đúng Picture Lock.')}>Duyệt</button>}</div>)}
        <button disabled={busy || !locks.some(lock => lock.validity === 'CURRENT')} onClick={() => act(() => api(`/shots/${shot.id}/media/dialogue/silence`, { picture_lock_id: locks.find(lock => lock.validity === 'CURRENT')?.id }), 'Đã tạo Dialogue im lặng cho cảnh không có thoại; cần review stem.')}>Dialogue im lặng cho cảnh không thoại</button>
        {stems.filter(s => s.validity === 'CURRENT').map(stem => <label className="field" key={`listen-${stem.id}`}>Nghe riêng {stem.role} v{stem.version}<audio controls preload="none" src={`/api/media/stems/${encodeURIComponent(stem.id)}/file`} /></label>)}
        {foleyJobs.filter(job => job.shot_id === shot.id).map(job => <div className="review-row" key={job.id}><p className="help">Foley {stateLabel(job.state)} · {job.error ?? job.operation_id.slice(0, 12)}</p>{job.state === 'RECONCILING' && <button disabled={busy} onClick={() => act(() => api(`/media/foley/jobs/${job.id}/reconcile`, {}), 'Đã kiểm tra receipt Foley; không tạo lại output.')}>Đối soát Foley</button>}</div>)}
      </section>
    </>}

    {episodeId && <section className="panel"><div className="panel-heading"><div><h3>Bàn giao Resolve</h3><p>FCP7 XML dự kiến cho thao tác import thủ công; chưa xác nhận trên Resolve Free/Studio của bạn.</p></div><span className="status warning">{deliveryGate?.allowed ? 'FILE SẴN SÀNG' : 'BỊ CHẶN'}</span></div>
      <div className="planner-fields"><label className="field">Chiều rộng<input type="number" value={profile.width} onChange={e => setProfile(p => ({ ...p, width: Number(e.target.value) }))} /></label><label className="field">Chiều cao<input type="number" value={profile.height} onChange={e => setProfile(p => ({ ...p, height: Number(e.target.value) }))} /></label><label className="field">Color profile<input value={profile.color_profile} placeholder="Nhập profile màu đã kiểm chứng" onChange={e => setProfile(p => ({ ...p, color_profile: e.target.value }))} /></label><label className="field">Sample rate<select value={profile.audio_sample_rate} onChange={e => setProfile(p => ({ ...p, audio_sample_rate: Number(e.target.value) }))}><option value={44100}>44.1 kHz</option><option value={48000}>48 kHz</option><option value={96000}>96 kHz</option></select></label><label className="field">Timecode đầu (HH:MM:SS:FF)<input value={profile.timecode_start} onChange={e => setProfile(p => ({ ...p, timecode_start: e.target.value }))} /></label></div>
      <button disabled={busy || !profile.color_profile.trim()} onClick={() => act(() => api(`/episodes/${episodeId}/media/delivery/profile`, { expected_revision: profile.revision, width: profile.width, height: profile.height, color_profile: profile.color_profile, audio_sample_rate: profile.audio_sample_rate, timecode_start: profile.timecode_start }), 'Đã lưu và duyệt profile bàn giao đúng phiên bản. Áp dụng color profile thủ công trong Resolve.')}>Lưu & duyệt profile bàn giao</button>
      {deliveryGate?.blockers.map((item, i) => <p className="help" key={i}>• {item}</p>)}
      <button disabled={busy || !deliveryGate?.allowed} onClick={() => act(() => api(`/episodes/${episodeId}/media/delivery/export`, {}), 'Đã xếp việc xuất package; tải lại để xem tiến độ. Cần import, relink và QC thủ công trong Resolve.')}>Xuất package</button>
      {packages.map(item => <div className="review-row" key={item.id}><p className="help">v{item.version} · {stateLabel(item.state)} · <code>{item.relative_path}</code></p>{['RECONCILING', 'PARTIAL_COPY', 'CORRUPT'].includes(item.state) && <button disabled={busy} onClick={() => act(() => api(`/media/delivery/packages/${item.id}/reconcile`, {}), 'Đã đối soát file package; output cũ được giữ.')}>Đối soát package</button>}</div>)}
      <p className="help">Gói gồm inventory/checksum đường dẫn tương đối, timeline.xml, 5 stem và hướng dẫn import. Sau khi di chuyển thư mục, relink theo inventory.json.</p>
    </section>}
  </div>
}
