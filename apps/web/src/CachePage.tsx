import { useEffect, useState } from 'react'
import { api, type Artifact, type Asset, type Shot } from './api'
import { errorLabel, subsystemLabel } from './uiText'

type CacheKind = 'hair' | 'cloth' | 'vfx'
type CacheSource = {
  body_artifact_id: string; asset_version_id: string; collision_fingerprint: string; wind_fingerprint: string
  physics_fingerprint: string; seed: number; ue_version: string; plugin_version: string
  view_lod_dependency: boolean; camera_fingerprint: string | null; warmup_frames: number; handle_before: number; handle_after: number
}
type CacheRow = { id: string; subsystem: CacheKind; version: number; validity: string; review: string; playback_status: string; output_sha256: string; relative_path: string; range_in: number; range_out_exclusive: number }
type CacheStatus = Record<CacheKind, { status: string; version?: number; required_range?: number[]; blockers: string[] }>
type Guard = { allowed: boolean; blockers: string[]; capability: string }
type Inventory = { id: string; kind: string; version: number; state: string; validity: string; provenance: string; inventory: { frames: unknown[]; missing: number[]; expected_count: number } }

const kindAsset: Record<CacheKind, string> = { hair: 'groom', cloth: 'costume', vfx: 'niagara_effect' }
const blankSource: CacheSource = {
  body_artifact_id: '', asset_version_id: '', collision_fingerprint: '', wind_fingerprint: '', physics_fingerprint: '',
  seed: 0, ue_version: '', plugin_version: '', view_lod_dependency: false, camera_fingerprint: null,
  warmup_frames: 0, handle_before: 0, handle_after: 0
}

export default function CachePage({ shot, projectId, assets, artifacts }: { shot: Shot; projectId: string; assets: Asset[]; artifacts: Artifact[] }) {
  const [kind, setKind] = useState<CacheKind>('hair')
  const [source, setSource] = useState<CacheSource>(blankSource)
  const [cameraFingerprint, setCameraFingerprint] = useState('')
  const [configuredSources, setConfiguredSources] = useState<Partial<Record<CacheKind, CacheSource>>>({})
  const [status, setStatus] = useState<CacheStatus | null>(null)
  const [caches, setCaches] = useState<CacheRow[]>([])
  const [guard, setGuard] = useState<Guard | null>(null)
  const [picture, setPicture] = useState<{ blockers: string[] } | null>(null)
  const [inventories, setInventories] = useState<Inventory[]>([])
  const [relativeFile, setRelativeFile] = useState('')
  const [sha256, setSha256] = useState('')
  const [rangeIn, setRangeIn] = useState(shot.manifest.timeline.in)
  const [rangeOut, setRangeOut] = useState(shot.manifest.timeline.out_exclusive)
  const [renderKind, setRenderKind] = useState<'PROXY' | 'FINAL'>('PROXY')
  const [renderFolder, setRenderFolder] = useState('')
  const [color, setColor] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [sourceImpact, setSourceImpact] = useState<{ input_changed: boolean; stale_cache_versions: { id: string; version: number }[]; affected_subsystems: string[] } | null>(null)

  async function load() {
    const [st, ca, gr, inv, hints] = await Promise.all([
      api<CacheStatus>(`/shots/${shot.id}/simulation`), api<CacheRow[]>(`/shots/${shot.id}/simulation/caches`),
      api<Guard>(`/shots/${shot.id}/render/final/preflight`), api<Inventory[]>(`/shots/${shot.id}/render/inventories`),
      api<{ camera_fingerprint: string; configured_sources: Partial<Record<CacheKind, { source: CacheSource }>> }>(`/shots/${shot.id}/simulation/hints`)
    ])
    const configured = Object.fromEntries(Object.entries(hints.configured_sources).map(([key, value]) => [key, value.source])) as Partial<Record<CacheKind, CacheSource>>
    setStatus(st); setCaches(ca); setGuard(gr); setInventories(inv); setCameraFingerprint(hints.camera_fingerprint); setConfiguredSources(configured)
    if (configured[kind]) setSource(configured[kind])
  }
  useEffect(() => { load().catch(e => setError(errorLabel(e))) }, [shot.id, shot.current_revision])

  async function run(work: () => Promise<unknown>, success: string, reload = true) {
    setError(''); setNotice('')
    try { await work(); if (reload) await load(); setNotice(success) } catch (e) { setError(errorLabel(e)) }
  }
  function update<K extends keyof CacheSource>(field: K, value: CacheSource[K]) { setSource(prev => ({ ...prev, [field]: value })) }
  function resolvedSource(): CacheSource { return { ...source, camera_fingerprint: source.view_lod_dependency ? cameraFingerprint : null } }
  async function browse(outputKind: 'cache_file' | 'render_folder') {
    try {
      const picked = await api<{ relative_path: string | null; sha256: string | null }>(`/projects/${projectId}/outputs/browse`, { kind: outputKind })
      if (picked.relative_path && outputKind === 'cache_file') { setRelativeFile(picked.relative_path); setSha256(picked.sha256 ?? '') }
      if (picked.relative_path && outputKind === 'render_folder') setRenderFolder(picked.relative_path)
    } catch (e) { setError(errorLabel(e)) }
  }
  const required = [shot.manifest.timeline.in - source.warmup_frames - source.handle_before,
                    shot.manifest.timeline.out_exclusive + source.handle_after]
  const cacheAssets = assets.filter(a => a.kind === kindAsset[kind])
  const bodies = artifacts.filter(a => a.shot_id === shot.id && a.role === 'body' && a.validity === 'CURRENT')

  return <div className="stack">
    <div className="simulation-note"><span className="mode-dot" /><p><strong>Phase 4 đang chờ kiểm chứng UE.</strong> Có thể cấu hình nguồn, kiểm tra file và xem guard trong app. Chưa có bake/playback cache hoặc MRQ final thật. File local đã duyệt vẫn có playback UNVERIFIED.</p></div>
    {error && <div className="preflight blocked" role="alert">{error}</div>}{notice && <div className="preflight ready">{notice}</div>}
    <section className="panel"><div className="panel-heading"><div><h3>Simulation cache · {shot.code}</h3><p>Đăng ký nguồn chính xác, sau đó chọn file cache hiện có trong thư mục dự án. Không dùng thiếu cache làm N/A.</p></div></div>
      <div className="registry-form">
        <label className="field">Thành phần<select value={kind} onChange={e => { const next = e.target.value as CacheKind; setKind(next); setSource(configuredSources[next] ?? blankSource); setSourceImpact(null) }}><option value="hair">Tóc</option><option value="cloth">Trang phục</option><option value="vfx">Niagara quan trọng</option></select></label>
        <label className="field">Body artifact hiện hành<select value={source.body_artifact_id} onChange={e => update('body_artifact_id', e.target.value)}><option value="">Chọn body</option>{bodies.map(a => <option key={a.id} value={a.id}>v{a.version} · {a.provenance} · {a.id.slice(0, 8)}</option>)}</select></label>
        <label className="field">Asset {kindAsset[kind]}<select value={source.asset_version_id} onChange={e => update('asset_version_id', e.target.value)}><option value="">Chọn asset</option>{cacheAssets.map(a => <option key={a.id} value={a.id}>{a.registry_id} · v{a.version}</option>)}</select></label>
        <label className="field">Collision fingerprint<input value={source.collision_fingerprint} onChange={e => update('collision_fingerprint', e.target.value)} /></label>
        <label className="field">Wind fingerprint<input value={source.wind_fingerprint} onChange={e => update('wind_fingerprint', e.target.value)} /></label>
        <label className="field">Physics fingerprint<input value={source.physics_fingerprint} onChange={e => update('physics_fingerprint', e.target.value)} /></label>
        <label className="field">Seed<input type="number" min="0" value={source.seed} onChange={e => update('seed', Number(e.target.value))} /></label>
        <label className="field">UE version<input value={source.ue_version} onChange={e => update('ue_version', e.target.value)} /></label>
        <label className="field">Plugin version<input value={source.plugin_version} onChange={e => update('plugin_version', e.target.value)} /></label>
        <label className="field">Warm-up frame<input type="number" min="0" value={source.warmup_frames} onChange={e => update('warmup_frames', Number(e.target.value))} /></label>
        <label className="field">Handle trước<input type="number" min="0" value={source.handle_before} onChange={e => update('handle_before', Number(e.target.value))} /></label>
        <label className="field">Handle sau<input type="number" min="0" value={source.handle_after} onChange={e => update('handle_after', Number(e.target.value))} /></label>
      </div>
      <label className="field"><span><input type="checkbox" checked={source.view_lod_dependency} onChange={e => update('view_lod_dependency', e.target.checked)} /> Cache phụ thuộc camera/view/LOD</span></label>
      <p className="help">Camera fingerprint: {source.view_lod_dependency ? cameraFingerprint.slice(0, 16) + '…' : 'không áp dụng'} · Vùng cần phủ [{required[0]}, {required[1]}).</p>
      <div className="actions"><button onClick={() => run(async () => setSourceImpact(await api<{ input_changed: boolean; stale_cache_versions: { id: string; version: number }[]; affected_subsystems: string[] }>(`/shots/${shot.id}/simulation/source/preview`, { subsystem: kind, expected_shot_revision: shot.current_revision, source: resolvedSource() })), 'Đã tính tác động nguồn simulation.', false)}>Xem tác động</button><button className="button-primary" onClick={() => run(() => api(`/shots/${shot.id}/simulation/source`, { subsystem: kind, expected_shot_revision: shot.current_revision, source: resolvedSource() }), 'Đã lưu nguồn simulation và cập nhật tác động cache.')}>Lưu nguồn simulation</button></div>
      {sourceImpact && <div className="impact settings-result"><strong>{sourceImpact.input_changed ? 'Nguồn đã đổi' : 'Nguồn chưa đổi'}</strong><p>Cache sẽ STALE: {sourceImpact.stale_cache_versions.map(c => `v${c.version}`).join(', ') || 'không có'} · Thành phần ảnh hưởng: {sourceImpact.affected_subsystems.map(subsystemLabel).join(', ') || 'không có'}. File cũ được giữ.</p></div>}
      <div className="registry-form settings-result"><label className="field">File cache trong dự án<input value={relativeFile} onChange={e => setRelativeFile(e.target.value)} /></label><button onClick={() => browse('cache_file')}>Duyệt file</button><label className="field">SHA-256<input value={sha256} onChange={e => setSha256(e.target.value)} /></label><label className="field">Frame vào<input type="number" value={rangeIn} onChange={e => setRangeIn(Number(e.target.value))} /></label><label className="field">Frame ra (không gồm)<input type="number" value={rangeOut} onChange={e => setRangeOut(Number(e.target.value))} /></label><button onClick={() => run(() => api(`/shots/${shot.id}/simulation/cache`, { subsystem: kind, expected_shot_revision: shot.current_revision, source: resolvedSource(), range_in: rangeIn, range_out_exclusive: rangeOut, relative_path: relativeFile, expected_sha256: sha256 }), 'Đã kiểm checksum và đăng ký file; playback vẫn chưa xác minh.')}>Đăng ký file</button></div>
      {status && <div className="review-list settings-result">{(['hair', 'cloth', 'vfx'] as CacheKind[]).map(k => <div className="review-row" key={k}><strong>{subsystemLabel(k)}</strong><span>{status[k].status}{status[k].version ? ` · v${status[k].version}` : ''}</span><small>{status[k].blockers.join(' · ') || 'File/range/review hợp lệ'}</small></div>)}</div>}
      {caches.length > 0 && <div className="table-wrap settings-result"><table><thead><tr><th>Cache</th><th>Vùng</th><th>File</th><th>Hiệu lực</th><th>Duyệt file</th><th>Playback</th></tr></thead><tbody>{caches.map(c => <tr key={c.id}><td>{subsystemLabel(c.subsystem)} v{c.version}</td><td>[{c.range_in},{c.range_out_exclusive})</td><td title={c.output_sha256}>{c.relative_path}</td><td>{c.validity}</td><td><button disabled={c.review !== 'NEEDS_REVIEW' || c.validity !== 'CURRENT'} onClick={() => run(() => api(`/simulation/caches/${c.id}/review`, { expected_version: c.version }), 'Đã duyệt file/cache version. Cần kiểm playback UE riêng.')}>{c.review === 'APPROVED' ? 'Đã duyệt' : 'Duyệt file'}</button></td><td>{c.playback_status}</td></tr>)}</tbody></table></div>}
    </section>
    <section className="panel"><div className="panel-heading"><div><h3>Render & Picture Lock</h3><p>Proxy và final có lịch sử riêng; kiểm kê file theo frame và SHA-256. Đủ file vẫn cần receipt MRQ và người dùng duyệt final take.</p></div></div>
      <div className="actions"><button onClick={() => run(async () => setGuard(await api<Guard>(`/shots/${shot.id}/render/final/preflight`)), 'Đã kiểm tra guard final.', false)}>Kiểm tra final</button><button onClick={() => run(async () => setPicture(await api<{ blockers: string[] }>(`/shots/${shot.id}/picture-lock/preflight`)), 'Đã kiểm tra Picture Lock.', false)}>Kiểm tra Picture Lock</button></div>
      {guard && <div className="preflight blocked settings-result"><strong>Final: BLOCKED</strong><ul>{guard.blockers.map((reason, i) => <li key={i}>{reason}</li>)}</ul></div>}
      {picture && <div className="preflight blocked settings-result"><strong>Picture Lock: BLOCKED</strong><ul>{picture.blockers.map((reason, i) => <li key={i}>{reason}</li>)}</ul></div>}
      <div className="registry-form settings-result"><label className="field">Loại<select value={renderKind} onChange={e => setRenderKind(e.target.value as 'PROXY' | 'FINAL')}><option value="PROXY">Proxy</option><option value="FINAL">Final</option></select></label><label className="field">Thư mục frame trong dự án<input value={renderFolder} onChange={e => setRenderFolder(e.target.value)} /></label><button onClick={() => browse('render_folder')}>Duyệt thư mục</button><label className="field">Color profile<input value={color} onChange={e => setColor(e.target.value)} placeholder="Tên profile đã dùng" /></label><button onClick={() => run(() => api(`/shots/${shot.id}/render/inventory`, { kind: renderKind, expected_shot_revision: shot.current_revision, relative_folder: renderFolder, color_profile: color }), 'Đã kiểm kê frame; chưa xác nhận MRQ hoặc hoàn tất final.')}>Kiểm kê file</button></div>
      {inventories.length > 0 && <div className="table-wrap settings-result"><table><thead><tr><th>Loại</th><th>Bản</th><th>Frame thấy</th><th>Thiếu</th><th>Hiệu lực</th><th>Trạng thái</th></tr></thead><tbody>{inventories.map(r => <tr key={r.id}><td>{r.kind}</td><td>v{r.version}</td><td>{r.inventory.frames.length}/{r.inventory.expected_count}</td><td>{r.inventory.missing.length}</td><td>{r.validity}</td><td>{r.state} · {r.provenance}</td></tr>)}</tbody></table></div>}
    </section>
  </div>
}
