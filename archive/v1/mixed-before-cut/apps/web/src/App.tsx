import { useCallback, useEffect, useState } from 'react'
import { api, bootstrap, type Artifact, type Asset, type Episode, type FlowState, type Job, type Progress, type Preview, type Project, type Shot, type State, type UnrealJob, type UnrealPreview, type UnrealSettings, type VoiceJob, type VoiceTake, type Timing, type TimingPreview, type VoiceLockPreview } from './api'
import { adapterLabel, assetKindLabel, errorLabel, routeLabel, stateLabel, subsystemLabel } from './uiText'
import CachePage from './CachePage'
import PlannerPage from './PlannerPage'
import AISettingsPanel from './AISettingsPanel'
import MediaPage from './MediaPage'
import MediaSettingsPanel from './MediaSettingsPanel'
import AcceptancePage from './AcceptancePage'
import SeriesPage from './SeriesPage'
import ToolSettingsPanel from './ToolSettingsPanel'
import FoleyToolPanel from './FoleyToolPanel'
import ProductionPage from './ProductionPage'
import FinalsPage from './FinalsPage'
import VideoModelSettingsPanel from './VideoModelSettingsPanel'
import VoiceSettingsPanel from './VoiceSettingsPanel'
import EpisodePage from './EpisodePage'
import HomePage from './HomePage'
import StepTabs, { type StepDef } from './StepTabs'
import WorkflowBar from './WorkflowBar'
import buildInfo from '../../../resources/app-build.json'

type Page = 'home' | 'series' | 'episode' | 'production' | 'finals' | 'system' | 'settings' | 'legacy'
type LegacyTab = 'registry' | 'planner' | 'shots' | 'inspector' | 'review' | 'voice' | 'cache' | 'media' | 'jobs'
type Theme = 'day' | 'night'

// The four zones of the V4.5 diagram come first, in the order an episode moves through them.
const pages: { id: Page; zone?: string; label: string; description: string }[] = [
  { id: 'home', label: 'Tổng quan', description: 'Dự án, các tập và bản đồ workflow: việc đã duyệt và bước tiếp theo' },
  { id: 'series', zone: 'Khu 0', label: 'Thư viện series', description: 'Chuẩn kỹ thuật và ngân sách (S1), nhân vật và bối cảnh (S2), quét asset và mapping (S3, CS1), Style bible (S4, CS2)' },
  { id: 'episode', zone: 'Khu A', label: 'Tập phim', description: 'Kịch bản và breakdown (C1), thư viện của tập (C2), phương thức và chi phí (C3), thoại và timing (C4)' },
  { id: 'production', zone: 'Khu B', label: 'Dựng shot', description: 'Nhánh UE (C5), cache và proxy (C6), nhánh AI và Hybrid, rough cut và khóa dựng (C7)' },
  { id: 'finals', zone: 'Khu C', label: 'Final & xuất phim', description: 'Final và chuẩn hóa (C8), Foley và âm thanh (C9), gói timeline, QC và xuất phim (C10)' },
  { id: 'system', label: 'Hệ thống', description: 'Hàng đợi GPU dùng chung, nghiệm thu, sao lưu và phục hồi' },
  { id: 'settings', label: 'Cài đặt', description: 'Kết nối Unreal, dịch vụ AI, giọng thoại, model video, FFmpeg và Foley trên máy này' },
  { id: 'legacy', label: 'Workflow cũ (V1)', description: 'Các màn hình của luồng V1, giữ cho dữ liệu chưa chuyển sang V4.5' }
]
const workflowPages = ['series', 'episode', 'production', 'finals']
const legacyTabs: StepDef[] = [
  { id: 'shots', label: 'Bảng cảnh' }, { id: 'inspector', label: 'Chi tiết & khóa' }, { id: 'review', label: 'Duyệt' }, { id: 'registry', label: 'Thư viện ID' },
  { id: 'planner', label: 'Lập kế hoạch' }, { id: 'voice', label: 'Giọng & nhịp' }, { id: 'cache', label: 'Cache & render' }, { id: 'media', label: 'Video & bàn giao' }, { id: 'jobs', label: 'Công việc' }
]
const settingsSteps: StepDef[] = [{ id: 'unreal', label: 'Kết nối Unreal' }, { id: 'ai', label: 'Dịch vụ AI & giọng thoại' }, { id: 'video', label: 'Model video AI' }, { id: 'tools', label: 'Công cụ xử lý video & Foley local' }]
const stored = (key: string) => { try { return localStorage.getItem(key) } catch { return null } }
const remember = (key: string, value: string) => { try { localStorage.setItem(key, value) } catch { /* Session-only when browser storage is unavailable. */ } }

const assetKinds = ['character', 'environment', 'style_reference', 'anchor', 'animation', 'paired_animation', 'dialogue', 'voice_profile', 'retarget_profile', 'render_profile', 'groom', 'costume', 'niagara_effect']
const routes = ['UNREAL', 'SEEDANCE', 'HYBRID']
type GpuQueue = { lease: { owner_kind: string; state: string } | null; waiting: { jobs: number; unreal: number; foley: number; scans: number } }
const gpuOwnerLabel = (kind: string) => ({ UNREAL: 'lắp dựng Unreal', SCAN: 'quét asset Unreal', FOLEY: 'Foley', MRQ: 'render MRQ', CACHE: 'bake cache', KIMODO: 'Kimodo', MOTION: 'tạo motion', JOB: 'công việc mô phỏng' }[kind] ?? kind)
const initialManifest = JSON.stringify({
  schema_version: '1.0', shot_id: 'EP01_SQ010_SH010', fps: { num: 24, den: 1 },
  timeline: { in: 0, out_exclusive: 192 }, route: { suggested: 'UNREAL' },
  environment_id: 'ENVIRONMENT_ID', performances: []
}, null, 2)

function Empty({ title, detail }: { title: string; detail: string }) {
  return <div className="empty"><span className="empty-icon" aria-hidden="true">◇</span><strong>{title}</strong><p>{detail}</p></div>
}

function Status({ value }: { value: string }) {
  const tone = ['STALE', 'BLOCKED', 'FAILED'].includes(value) ? 'warning'
    : ['APPROVED', 'COMPLETED', 'CURRENT'].includes(value) ? 'success' : 'neutral'
  return <span className={`status ${tone}`}>{stateLabel(value)}</span>
}

function ShotPicker({ shots, value, onChange }: { shots: Shot[]; value: string; onChange: (value: string) => void }) {
  return <label className="field shot-picker">Cảnh quay
    <select value={value} onChange={e => onChange(e.target.value)}>
      <option value="">Chọn cảnh quay</option>
      {shots.map(s => <option key={s.id} value={s.id}>{s.code} · phiên bản {s.current_revision}</option>)}
    </select>
  </label>
}

export default function App() {
  const [page, setPage] = useState<Page>('home')
  const [legacyTab, setLegacyTab] = useState<LegacyTab>('shots')
  const [steps, setSteps] = useState<Record<string, string>>({ series: 's1', episode: 'breakdown', production: 'ue', finals: 'final', settings: 'unreal' })
  const [episodeId, setEpisodeId] = useState('')
  const [progress, setProgress] = useState<Progress | null>(null)
  const [flowVersion, setFlowVersion] = useState(0)
  const [theme, setTheme] = useState<Theme>(() => document.documentElement.dataset.theme === 'day' ? 'day' : 'night')
  const [ready, setReady] = useState(false)
  const [serverBuild, setServerBuild] = useState<string | null>(null)
  const [unrealConfigured, setUnrealConfigured] = useState(false)
  const [unrealSettings, setUnrealSettings] = useState<UnrealSettings | null>(null)
  const [editorPath, setEditorPath] = useState('')
  const [unrealProjectPath, setUnrealProjectPath] = useState('')
  const [settingsChecked, setSettingsChecked] = useState(false)
  const [settingsBusy, setSettingsBusy] = useState(false)
  const [settingsError, setSettingsError] = useState('')
  const [settingsNotice, setSettingsNotice] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [projects, setProjects] = useState<Project[]>([])
  const [projectId, setProjectId] = useState('')
  const [episodes, setEpisodes] = useState<Episode[]>([])
  const [assets, setAssets] = useState<Asset[]>([])
  const [shots, setShots] = useState<Shot[]>([])
  const [jobs, setJobs] = useState<Job[]>([])
  const [unrealJobs, setUnrealJobs] = useState<UnrealJob[]>([])
  const [unrealWorkerStopped, setUnrealWorkerStopped] = useState(false)
  const [voiceJobs, setVoiceJobs] = useState<VoiceJob[]>([])
  const [voiceTakes, setVoiceTakes] = useState<VoiceTake[]>([])
  const [timings, setTimings] = useState<Timing[]>([])
  const [timingPreview, setTimingPreview] = useState<TimingPreview | null>(null)
  const [voicePerformanceId, setVoicePerformanceId] = useState('')
  const [voiceLockPreview, setVoiceLockPreview] = useState<VoiceLockPreview | null>(null)
  const [cameraPlan, setCameraPlan] = useState<{ mode: string; subjects: string[]; capability: string } | null>(null)
  const [facialPlan, setFacialPlan] = useState<{ timing_version: number; placements: Timing['placements']; capability: string } | null>(null)
  const [unrealEpisodeId, setUnrealEpisodeId] = useState('')
  const [unrealShotCodes, setUnrealShotCodes] = useState('')
  const [unrealPreview, setUnrealPreview] = useState<UnrealPreview | null>(null)
  const [unrealPreflight, setUnrealPreflight] = useState(false)
  const [unrealDiff, setUnrealDiff] = useState<{ action: string; path: string; character_id?: string }[]>([])
  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [gpuQueue, setGpuQueue] = useState<GpuQueue | null>(null)
  const [shotId, setShotId] = useState('')
  const [newEpisodeId, setNewEpisodeId] = useState('')
  const [assetId, setAssetId] = useState('')
  const [assetKind, setAssetKind] = useState('character')
  const [assetMetadata, setAssetMetadata] = useState('{}')
  const [motionSkeleton, setMotionSkeleton] = useState('')
  const [motionTags, setMotionTags] = useState('')
  const [motionMatch, setMotionMatch] = useState<Asset | null>(null)
  const [draftManifestText, setDraftManifestText] = useState(initialManifest)
  const [manifestText, setManifestText] = useState(initialManifest)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [approvedRoute, setApprovedRoute] = useState('UNREAL')
  const [buildSubsystem, setBuildSubsystem] = useState('body')
  const [preflight, setPreflight] = useState<{ allowed: boolean; blockers: string[] } | null>(null)
  const [adoptFingerprint, setAdoptFingerprint] = useState('')

  const selected = shots.find(s => s.id === shotId)
  const project = projects.find(p => p.id === projectId)
  const activePage = pages.find(p => p.id === page)!
  const legacyProject = project?.workflow_version === 'V1'
  const hasLegacyData = shots.length > 0 || jobs.length > 0 || assets.length > 0
  const showLegacy = Boolean(project) && (legacyProject || hasLegacyData)
  const legacy = (tab: LegacyTab) => page === 'legacy' && legacyTab === tab
  const go = (target: string, step?: string) => { setPage(target as Page); if (step) setSteps(current => ({ ...current, [target]: step })); window.scrollTo({ top: 0 }) }
  const setStep = (target: string) => (step: string) => setSteps(current => ({ ...current, [target]: step }))
  const marks = (target: string): Record<string, FlowState> => Object.fromEntries((progress?.zones.find(z => z.page === target)?.checkpoints ?? []).map(c => [c.step, c.state]))
  const unrealPathsComplete = Boolean(editorPath.trim() && unrealProjectPath.trim())
  const unrealDraftChanged = Boolean(unrealSettings && (
    editorPath.trim() !== unrealSettings.editor_path || unrealProjectPath.trim() !== unrealSettings.project_path
  ))
  const unrealSaveHint = !unrealSettings ? 'Chưa tải được cấu hình. Bấm Tải lại để thử lại.'
    : !unrealPathsComplete ? 'Chọn đủ tệp Unreal Editor và tệp .uproject để bật nút Lưu cấu hình.'
    : 'Có thể bấm Lưu cấu hình ngay. Ứng dụng tự kiểm tra đường dẫn và plugin trước khi lưu.'

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try { localStorage.setItem('studio_theme', theme) } catch { /* Session-only when browser storage is unavailable. */ }
  }, [theme])

  const refresh = useCallback(async (pid = projectId) => {
    const all = await api<Project[]>('/projects')
    setProjects(all)
    if (!pid) {
      setEpisodes([]); setAssets([]); setShots([]); setJobs([]); setUnrealJobs([]); setVoiceJobs([]); setVoiceTakes([]); setTimings([]); setArtifacts([])
      return all
    }
    const [ep, reg, sh, jo, ar, uj, vj] = await Promise.all([
      api<Episode[]>(`/projects/${pid}/episodes`), api<Asset[]>(`/projects/${pid}/registry`),
      api<Shot[]>(`/projects/${pid}/shots`), api<Job[]>(`/projects/${pid}/jobs`),
      api<Artifact[]>(`/projects/${pid}/artifacts`), api<UnrealJob[]>(`/projects/${pid}/unreal/jobs`),
      api<VoiceJob[]>(`/projects/${pid}/voice/jobs`)
    ])
    setGpuQueue(await api<GpuQueue>('/resources/gpu-queue'))
    setEpisodes(ep); setAssets(reg); setShots(sh); setJobs(jo); setArtifacts(ar); setUnrealJobs(uj); setVoiceJobs(vj)
    if (shotId && sh.some(s => s.id === shotId)) {
      const [takes, currentTimings] = await Promise.all([api<VoiceTake[]>(`/shots/${shotId}/voice/takes`), api<Timing[]>(`/shots/${shotId}/timing`)])
      setVoiceTakes(takes); setTimings(currentTimings)
    } else { setVoiceTakes([]); setTimings([]) }
    return all
  }, [projectId, shotId])

  useEffect(() => {
    bootstrap().then(async session => {
      setServerBuild(session.build_id); await loadUnrealSettings(); setReady(true)
      const all = await refresh()
      // Reopen the project the operator was working on.
      const last = stored('studio_project')
      if (last && all.some(p => p.id === last)) setProjectId(last)
    }).catch(e => setError(errorLabel(e)))
  }, [])

  useEffect(() => {
    if (!projectId || !ready) return
    refresh(projectId).catch(e => setError(errorLabel(e)))
    const stream = new EventSource(`/api/events/stream?from_latest=true&project_id=${encodeURIComponent(projectId)}`)
    let timer = 0
    // Events come in bursts while a job runs; one reload per burst is enough.
    stream.onmessage = () => { window.clearTimeout(timer); timer = window.setTimeout(() => { refresh(projectId).catch(e => setError(errorLabel(e))); setFlowVersion(v => v + 1) }, 400) }
    return () => { window.clearTimeout(timer); stream.close() }
  }, [projectId, shotId, ready])

  // One working episode for every workflow tab; a project with a single episode needs no choosing.
  useEffect(() => {
    if (episodeId && !episodes.some(e => e.id === episodeId)) { setEpisodeId(''); return }
    if (!episodeId && episodes.length) {
      const pick = episodes.find(e => e.id === stored('studio_episode')) ?? (episodes.length === 1 ? episodes[0] : undefined)
      if (pick) setEpisodeId(pick.id)
    }
  }, [episodes])
  useEffect(() => { if (projectId) remember('studio_project', projectId) }, [projectId])
  useEffect(() => { if (episodeId) remember('studio_episode', episodeId) }, [episodeId])
  useEffect(() => { if (page === 'legacy' && !showLegacy) setPage('home') }, [page, showLegacy])

  useEffect(() => {
    if (!projectId || !ready || project?.workflow_version !== 'V4_5') { setProgress(null); return }
    let live = true
    api<Progress>(`/projects/${projectId}/progress${episodeId ? `?episode_id=${episodeId}` : ''}`).then(value => { if (live) setProgress(value) }).catch(() => undefined)
    return () => { live = false }
  }, [projectId, episodeId, flowVersion, page, steps, ready, project?.workflow_version])

  useEffect(() => {
    if (selected) {
      setManifestText(JSON.stringify(selected.manifest, null, 2))
      setApprovedRoute(selected.approval?.decision.approved_route || selected.manifest.route?.suggested || 'UNREAL')
      setPreview(null); setPreflight(null)
      setTimingPreview(null); setCameraPlan(null); setFacialPlan(null)
      setVoiceLockPreview(null)
      setVoicePerformanceId(selected.manifest.performances?.find((p: Record<string, unknown>) => p.voice_profile_id)?.performance_id ?? '')
    }
  }, [shotId, selected?.current_revision])

  async function act(action: () => Promise<unknown>, message: string, pid = projectId) {
    setError(''); setNotice('')
    try {
      await action()
      await refresh(pid)
      setNotice(message)
    } catch (e) { setError(errorLabel(e)) }
  }

  async function loadUnrealSettings() {
    const value = await api<UnrealSettings>('/settings/unreal')
    setUnrealSettings(value)
    setEditorPath(value.editor_path)
    setUnrealProjectPath(value.project_path)
    setUnrealConfigured(value.configured)
    setSettingsChecked(false)
    setSettingsError(''); setSettingsNotice('')
  }

  async function browseUnreal(kind: 'editor' | 'project') {
    setError(''); setNotice(''); setSettingsError(''); setSettingsBusy(true)
    setSettingsNotice('Đang mở hộp thoại chọn tệp Windows. Chọn tệp hoặc bấm Hủy để quay lại.')
    try {
      const picked = await api<{ path: string | null }>('/settings/unreal/browse', { kind })
      if (picked.path) {
        if (kind === 'editor') setEditorPath(picked.path)
        else setUnrealProjectPath(picked.path)
        setSettingsChecked(false)
        setSettingsNotice('Đã chọn tệp. Khi đã chọn đủ hai tệp, bấm Lưu cấu hình.')
      } else {
        setSettingsNotice('Đã hủy chọn tệp. Đường dẫn hiện tại được giữ nguyên.')
      }
    } catch (e) { setSettingsNotice(''); setSettingsError(errorLabel(e)) }
    finally { setSettingsBusy(false) }
  }

  async function checkSettings() {
    setError(''); setNotice(''); setSettingsError(''); setSettingsNotice('Đang kiểm tra đường dẫn và plugin…'); setSettingsBusy(true)
    try {
      const result = await api<{ message: string }>('/settings/unreal/validate', {
        editor_path: editorPath, project_path: unrealProjectPath
      })
      setSettingsChecked(true)
      setSettingsNotice(result.message)
    } catch (e) { setSettingsChecked(false); setSettingsNotice(''); setSettingsError(errorLabel(e)) }
    finally { setSettingsBusy(false) }
  }

  async function saveSettings() {
    if (!unrealSettings || !unrealPathsComplete || settingsBusy) return
    setError(''); setNotice(''); setSettingsError(''); setSettingsNotice('Đang kiểm tra và lưu cấu hình…'); setSettingsBusy(true)
    try {
      const saved = await api<UnrealSettings>('/settings/unreal', {
        editor_path: editorPath, project_path: unrealProjectPath, expected_revision: unrealSettings.revision
      })
      setUnrealSettings(saved)
      setEditorPath(saved.editor_path)
      setUnrealProjectPath(saved.project_path)
      setUnrealConfigured(saved.configured)
      setUnrealPreview(null); setUnrealPreflight(false)
      setSettingsChecked(false)
      setSettingsNotice('Đã lưu cấu hình Unreal trên máy này. Cấu hình được giữ sau khi khởi động lại; việc chạy trên project và asset thật cần kiểm chứng riêng.')
    } catch (e) { setSettingsChecked(false); setSettingsNotice(''); setSettingsError(errorLabel(e)) }
    finally { setSettingsBusy(false) }
  }

  async function reloadSettings() {
    setSettingsBusy(true); setSettingsError(''); setSettingsNotice('Đang tải cấu hình đã lưu…')
    try {
      const session = await bootstrap()
      setServerBuild(session.build_id)
      await loadUnrealSettings()
      setSettingsNotice('Đã tải cấu hình đang lưu trên máy này.')
    } catch (e) { setSettingsNotice(''); setSettingsError(errorLabel(e)) }
    finally { setSettingsBusy(false) }
  }

  async function checkUnreal(preflight: boolean) {
    if (!unrealEpisodeId) return
    setError(''); setNotice('')
    try {
      if (preflight) {
        const check = await api<{ changes: { action: string; path: string; character_id?: string }[] }>(`/projects/${projectId}/episodes/${unrealEpisodeId}/unreal/preflight`, { shot_codes: unrealShotCodes.split(',').map(s => s.trim()).filter(Boolean) })
        setUnrealDiff(check.changes)
        setUnrealPreflight(true)
        setNotice('Unreal đã kiểm tra tài nguyên thật. Hãy xem diff trước khi xếp việc.')
      } else {
        setUnrealPreview(await api<UnrealPreview>(`/projects/${projectId}/episodes/${unrealEpisodeId}/unreal/preview`, { shot_codes: unrealShotCodes.split(',').map(s => s.trim()).filter(Boolean) }))
        setUnrealDiff([])
        setUnrealPreflight(false)
      }
    } catch (e) { setUnrealPreflight(false); setError(errorLabel(e)) }
  }

  async function createProject(name: string) {
    await act(async () => {
      const created = await api<Project>('/projects', { name })
      setProjectId(created.id)
    }, 'Đã tạo dự án. Thêm tập đầu tiên rồi bắt đầu ở Khu 0 · Thư viện series.', '')
  }

  async function createShot() {
    await act(async () => {
      const manifest = JSON.parse(draftManifestText)
      await api(`/projects/${projectId}/shots`, { episode_id: newEpisodeId, ordinal: shots.length, manifest })
    }, 'Đã tạo bản nháp cảnh quay.')
  }

  async function addAsset() {
    await act(async () => {
      const metadata = JSON.parse(assetMetadata)
      await api(`/projects/${projectId}/registry`, { registry_id: assetId, kind: assetKind, metadata })
    }, 'Đã lưu phiên bản tài nguyên.')
  }

  async function searchMotion() {
    setError(''); setMotionMatch(null)
    try {
      setMotionMatch(await api<Asset>(`/projects/${projectId}/motion/search`, {
        skeleton_id: motionSkeleton.trim(), tags: motionTags.split(',').map(tag => tag.trim()).filter(Boolean)
      }))
    } catch (e) { setError(errorLabel(e)) }
  }

  async function changeState(state: State, mode: State['mode'], lock_state: State['lock_state'], adopt = false) {
    if (!selected) return
    await act(() => api(`/shots/${selected.id}/subsystems/${state.subsystem}`, {
      expected_state_revision: state.state_revision, mode, lock_state,
      adopt_fingerprint: adopt ? adoptFingerprint : null
    }), `Đã lưu trạng thái ${subsystemLabel(state.subsystem).toLowerCase()}.`)
  }

  async function previewEdit(save: boolean) {
    if (!selected) return
    setError(''); setNotice('')
    try {
      const body = { expected_revision: selected.current_revision, manifest: JSON.parse(manifestText) }
      if (save) await act(() => api(`/shots/${selected.id}/revisions`, body), 'Đã tạo phiên bản cảnh quay mới.')
      else setPreview(await api<Preview>(`/shots/${selected.id}/revisions/preview`, body))
    } catch (e) { setError(errorLabel(e)) }
  }

  async function checkPreflight() {
    if (!selected) return
    setError('')
    try {
      setPreflight(await api(`/shots/${selected.id}/preflight`, {
        revision: selected.current_revision, subsystem: buildSubsystem
      }))
    } catch (e) { setError(errorLabel(e)) }
  }

  async function previewVoiceTiming() {
    if (!selected) return
    setError(''); setNotice('')
    try { setTimingPreview(await api<TimingPreview>(`/shots/${selected.id}/timing/preview`)) }
    catch (e) { setError(errorLabel(e)) }
  }

  async function previewVoiceLock(takeId: string) {
    setError(''); setNotice('')
    try { setVoiceLockPreview(await api<VoiceLockPreview>(`/voice/takes/${takeId}/lock/preview`)) }
    catch (e) { setVoiceLockPreview(null); setError(errorLabel(e)) }
  }

  async function showP3Plan(kind: 'facial' | 'camera') {
    if (!selected) return
    setError(''); setNotice('')
    try {
      if (kind === 'facial') setFacialPlan(await api(`/shots/${selected.id}/facial/plan`))
      else setCameraPlan(await api(`/shots/${selected.id}/camera/plan`))
    } catch (e) { setError(errorLabel(e)) }
  }

  if (!ready) return <main className="boot">
    <div className="brand-mark">A</div><h1>AI Animation Studio</h1>
    {error ? <><p className="alert error" role="alert">{error}</p><button onClick={() => window.location.reload()}>Thử lại</button></> : <p>Đang mở phiên làm việc trên máy…</p>}
  </main>

  return <div className="shell">
    <header className="topbar">
      <div className="brand"><div className="brand-mark" aria-hidden="true">A</div><div><p className="eyebrow">KHÔNG GIAN SẢN XUẤT</p><h1>AI Animation Studio</h1><p className="brand-sub">Điều phối sản xuất phim</p></div></div>
      <div className="topbar-actions"><button type="button" className="theme-toggle" role="switch" aria-checked={theme === 'night'} aria-label="Chế độ ban đêm" title={theme === 'night' ? 'Bật chế độ ban ngày' : 'Bật chế độ ban đêm'} onClick={() => setTheme(current => current === 'night' ? 'day' : 'night')}><span className="theme-switch-track"><span className="theme-switch-thumb" /></span><span>{theme === 'night' ? 'Ban đêm' : 'Ban ngày'}</span></button><div className="mode-banner"><span className="mode-dot" /><div><strong>{unrealConfigured ? 'UNREAL ĐÃ CẤU HÌNH' : 'UNREAL CHƯA CẤU HÌNH'}</strong><small>{unrealConfigured ? 'Cần kiểm chứng trên project và asset thật' : 'Công việc mô phỏng vẫn hoạt động; chưa ghi vào Unreal'}</small></div><button type="button" className="mode-action" onClick={() => go('settings', 'unreal')}>Thiết lập</button></div></div>
    </header>

    <nav className="tabs" aria-label="Các khu vực làm việc">
      {pages.filter(p => p.id !== 'legacy' || showLegacy).map(p => <button key={p.id} type="button" className={`${page === p.id ? 'active' : ''} ${p.id === 'system' ? 'tab-gap' : ''} ${p.id === 'legacy' ? 'tab-legacy' : ''}`} aria-current={page === p.id ? 'page' : undefined} title={p.description} onClick={() => go(p.id)}>{p.zone && <span className="tab-zone">{p.zone}</span>}{p.label}</button>)}
    </nav>

    <div className="workspace-bar">
      <label className="field project-select">Dự án đang làm
        <select value={projectId} onChange={e => { setProjectId(e.target.value); setEpisodeId(''); setProgress(null); setShotId(''); setNewEpisodeId(''); setUnrealEpisodeId(''); setUnrealPreview(null); setUnrealPreflight(false); setPreflight(null); setPreview(null); setTimingPreview(null); setError(''); setNotice(''); setEpisodes([]); setAssets([]); setShots([]); setJobs([]); setUnrealJobs([]); setVoiceJobs([]); setVoiceTakes([]); setTimings([]); setArtifacts([]) }}>
          <option value="">Chọn dự án</option>
          {projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
      </label>
      {project && <label className="field episode-select">Tập đang làm
        <select value={episodeId} onChange={e => setEpisodeId(e.target.value)}>
          <option value="">{episodes.length ? 'Chọn tập' : 'Chưa có tập'}</option>
          {episodes.map(e => <option key={e.id} value={e.id}>{e.name}</option>)}
        </select>
      </label>}
      <button type="button" className="button-quiet" onClick={() => refresh().then(() => setFlowVersion(v => v + 1)).catch(e => setError(errorLabel(e)))}>Tải lại</button>
      {project && <div className="workspace-stats"><span>{legacyProject ? 'Workflow cũ (V1)' : 'Workflow V4.5'}</span><span>{episodes.length} tập</span>{showLegacy && <span>{shots.length} cảnh V1</span>}</div>}
    </div>

    {error && <div className="alert error" role="alert">{error}</div>}
    {notice && <div className="alert notice" role="status">{notice}</div>}

    {workflowPages.includes(page) && <WorkflowBar progress={progress} page={page} gpu={gpuQueue?.lease ? `GPU đang bận: ${gpuOwnerLabel(gpuQueue.lease.owner_kind)}` : null} onGo={go} />}
    {project && legacyProject && workflowPages.includes(page) && <div className="preflight blocked flow-legacy-note"><span>Dự án này đang dùng workflow cũ (V1). Các bước V4.5 ở tab này chỉ duyệt được sau khi chuyển dự án sang V4.5.</span><button type="button" onClick={() => go('home')}>Mở Tổng quan để chuyển</button></div>}

    <main className="content">
      <div className="page-heading"><div><p className="eyebrow">{activePage.zone ? `${activePage.zone.toUpperCase()} · WORKFLOW V4.5` : 'AI ANIMATION STUDIO'}</p><h2>{activePage.label}</h2><p>{activePage.description}</p></div>{project && <span className="context-pill">{project.name}{episodeId && workflowPages.includes(page) ? ` · ${episodes.find(e => e.id === episodeId)?.name ?? ''}` : ''}</span>}</div>

      {page === 'settings' && <div className="stack">
        <StepTabs steps={settingsSteps} value={steps.settings} onChange={setStep('settings')} label="Nhóm cài đặt" />
        <div className="stack" hidden={steps.settings !== 'ai'}><AISettingsPanel /><VoiceSettingsPanel /></div>
        <div className="stack" hidden={steps.settings !== 'video'}><VideoModelSettingsPanel />{projects.some(item => item.workflow_version === 'V1') && <details className="scene-card"><summary><strong>Seedance của workflow cũ (V1)</strong><span className="muted">chỉ dùng cho dự án chưa chuyển sang V4.5</span></summary><MediaSettingsPanel /></details>}</div>
        <div className="stack" hidden={steps.settings !== 'tools'}><ToolSettingsPanel /><FoleyToolPanel /></div>
        <div className="settings-layout" hidden={steps.settings !== 'unreal'}>
        <section className="panel" aria-busy={settingsBusy}><div className="panel-heading"><div><h3>Kết nối Unreal trên máy này</h3><p>Chọn hai tệp bằng nút Duyệt rồi bấm Lưu cấu hình. Ứng dụng kiểm tra trước khi lưu; nút Kiểm tra đường dẫn dùng để kiểm tra riêng.</p></div><span className={`status ${settingsError || unrealSettings?.error ? 'warning' : unrealDraftChanged || !unrealConfigured ? 'neutral' : 'success'}`}>{unrealDraftChanged ? 'Chưa lưu' : unrealSettings?.error ? 'Cần sửa cấu hình' : unrealConfigured ? 'Đã lưu' : 'Chưa cấu hình'}</span></div>
          <p className="help">Bản giao diện: {buildInfo.build_id} · Máy chủ: {serverBuild ?? 'bản cũ chưa cung cấp phiên bản'}.</p>
          {ready && serverBuild !== buildInfo.build_id && <div className="alert error" role="alert">Máy chủ đang chạy khác phiên bản giao diện. Đóng cửa sổ launcher đang chạy, mở lại Start-Studio.cmd rồi tải lại trang để dùng bản cập nhật.</div>}
          <div className="settings-fields">
            <div className="setting-file"><label className="field grow">Unreal Editor (UnrealEditor-Cmd.exe hoặc UnrealEditor.exe)<input value={editorPath} disabled={settingsBusy} onChange={e => { setEditorPath(e.target.value); setSettingsChecked(false); setSettingsError(''); setSettingsNotice('') }} placeholder="Chưa chọn tệp Unreal Editor — bấm Duyệt…" aria-describedby="unreal-settings-help" spellCheck={false} /></label><button type="button" aria-label="Duyệt tệp Unreal Editor" disabled={settingsBusy} onClick={() => browseUnreal('editor')}>Duyệt…</button></div>
            <div className="setting-file"><label className="field grow">Project Unreal (.uproject)<input value={unrealProjectPath} disabled={settingsBusy} onChange={e => { setUnrealProjectPath(e.target.value); setSettingsChecked(false); setSettingsError(''); setSettingsNotice('') }} placeholder="Chưa chọn project Unreal — bấm Duyệt…" aria-describedby="unreal-settings-help" spellCheck={false} /></label><button type="button" aria-label="Duyệt project Unreal" disabled={settingsBusy} onClick={() => browseUnreal('project')}>Duyệt…</button></div>
          </div>
          <p id="unreal-settings-help" className="help">{unrealSaveHint}</p>
          <div className="actions"><button disabled={settingsBusy || !unrealPathsComplete} onClick={checkSettings}>Kiểm tra đường dẫn</button><button className="button-primary" disabled={settingsBusy || !unrealPathsComplete || !unrealSettings} title={unrealSaveHint} onClick={saveSettings}>Lưu cấu hình</button><button className="button-quiet" disabled={settingsBusy} onClick={reloadSettings}>Tải lại</button></div>
          {settingsError && <div className="alert error settings-result" role="alert">Không lưu hoặc kiểm tra được cấu hình: {settingsError}</div>}
          {settingsNotice && <div className="preflight ready settings-result" role="status">{settingsNotice}</div>}
          {unrealSettings?.error && <div className="preflight blocked settings-result">Cấu hình đã lưu cần sửa: {unrealSettings.error}</div>}
          {settingsChecked && <div className="preflight ready settings-result">Đường dẫn và plugin hợp lệ. Lưu cấu hình để dùng cho công việc Unreal.</div>}
          <p className="help">Nguồn hiện tại: {unrealSettings?.source === 'APP' ? 'đã lưu trong app' : unrealSettings?.editor_path || unrealSettings?.project_path ? 'cấu hình môi trường cũ' : 'chưa lưu cấu hình'}. Trạng thái này chưa xác nhận asset, Sequencer hoặc phiên bản Unreal thực tế.</p>
        </section>
        <section className="panel settings-guide"><div className="panel-heading"><div><h3>Cần chuẩn bị gì?</h3><p>Việc chọn đường dẫn không tự tạo project hay tài nguyên Unreal.</p></div></div><ol><li>Chọn Unreal Editor 5.8 đang cài trên máy. App viết cho UE 5.8; phiên bản khác bị chặn khi quét asset và lắp dựng.</li><li>Tạo hoặc mở project <code>.uproject</code> có map, nhân vật và animation thật.</li><li>Bật ba plugin: PythonScriptPlugin, EditorScriptingUtilities, SequencerScripting.</li><li>Vào Thư viện series để quét asset của project và chọn mapping (CS1); không cần gõ đường dẫn asset.</li></ol></section>
        </div>
      </div>}

      {page === 'home' && <HomePage project={project} episodes={episodes} episodeId={episodeId} progress={progress} hasLegacyData={hasLegacyData}
        onCreateProject={createProject} onSelectEpisode={setEpisodeId} onGo={go}
        onAddEpisode={name => act(async () => { const created = await api<Episode>(`/projects/${projectId}/episodes`, { name, ordinal: Math.max(0, ...episodes.map(e => e.ordinal)) + 1 }); setEpisodeId(created.id) }, 'Đã thêm tập và chọn làm tập đang làm.')}
        onUpgrade={() => act(async () => { await api(`/projects/${projectId}/workflow/upgrade`, { confirmed: true }); setFlowVersion(v => v + 1) }, 'Đã chuyển dự án sang workflow V4.5. Bắt đầu ở Khu 0 · Thư viện series.')} />}

      {page === 'series' && <SeriesPage projectId={projectId} episodes={episodes} episodeId={episodeId} unrealConfigured={unrealConfigured} step={steps.series} onStep={setStep('series')} marks={marks('series')} onOpenHome={() => go('home')} />}
      {page === 'episode' && <EpisodePage projectId={projectId} episodeId={episodeId} unrealConfigured={unrealConfigured} step={steps.episode} onStep={setStep('episode')} marks={marks('episode')} onOpenSeries={() => go('series', 'mapping')} />}
      {page === 'production' && <ProductionPage projectId={projectId} episodeId={episodeId} step={steps.production} onStep={setStep('production')} marks={marks('production')} />}
      {page === 'finals' && <FinalsPage projectId={projectId} episodeId={episodeId} step={steps.finals} onStep={setStep('finals')} marks={marks('finals')} />}

      {page === 'system' && <>
        <section className="panel"><div className="panel-heading"><div><h3>Hàng đợi GPU dùng chung</h3><p>Render Unreal, quét asset, Kimodo và Foley không chạy song song trên cùng GPU; việc đến sau chờ việc trước xong.</p></div><span className={`status ${gpuQueue?.lease ? (gpuQueue.lease.state === 'RECONCILING' ? 'warning' : 'neutral') : 'success'}`}>{gpuQueue?.lease ? `Đang giữ: ${gpuOwnerLabel(gpuQueue.lease.owner_kind)}` : 'Đang rảnh'}</span></div>
          {gpuQueue ? <p className="help">Đang chờ: {gpuQueue.waiting.unreal} lắp dựng Unreal · {gpuQueue.waiting.scans} quét asset · {gpuQueue.waiting.foley} Foley · {gpuQueue.waiting.jobs} việc khác.{gpuQueue.lease?.state === 'RECONCILING' ? ' Lần chạy trước bị gián đoạn: xác nhận ở đúng bước đang báo gián đoạn (dựng Unreal, render proxy/final hoặc Foley) để giải phóng GPU.' : ''}</p> : <p className="help">Chọn dự án để xem hàng đợi.</p>}
        </section>
        <AcceptancePage projectId={projectId} episodes={episodes} />
      </>}

      {page === 'legacy' && <>
        <div className="simulation-note"><span className="mode-dot" /><p><strong>Workflow cũ (V1).</strong> Các màn hình dưới đây thuộc luồng trước bản V4.5 và chỉ làm việc với dữ liệu V1 của dự án. Luồng hiện tại nằm ở bốn tab Khu 0, A, B, C.</p></div>
        <StepTabs steps={legacyTabs} value={legacyTab} onChange={tab => setLegacyTab(tab as LegacyTab)} label="Màn hình của workflow V1" />
      </>}

      {legacy('registry') && <section className="panel"><div className="panel-heading"><div><h3>Thư viện tài nguyên</h3><p>ID và thông tin tương thích do bạn nhập. Manifest nháp không được tự cấp đường dẫn Unreal hoặc quyền duyệt.</p></div><span className="count">{assets.length} phiên bản</span></div>
        {project ? <><div className="registry-form"><label className="field">ID tài nguyên<input value={assetId} onChange={e => setAssetId(e.target.value)} placeholder="Ví dụ: CH_Hero_A" /></label><label className="field">Loại tài nguyên<select value={assetKind} onChange={e => setAssetKind(e.target.value)}>{assetKinds.map(k => <option key={k} value={k}>{assetKindLabel(k)}</option>)}</select></label><label className="field metadata-field">Thông tin kỹ thuật (JSON)<textarea value={assetMetadata} onChange={e => setAssetMetadata(e.target.value)} rows={3} spellCheck={false} /></label><button className="button-primary" onClick={addAsset}>Lưu phiên bản</button></div>
          {assetKind === 'voice_profile' && <p className="help registry-hint">Giọng thử local: <code>{'{"character_id":"CAPTAIN","provider":"LOCAL_VOICE_FIXTURE","sample_rate":16000,"fixture_duration_ms":1000}'}</code>. Đây là âm mẫu tổng hợp, không phải giọng nói.</p>}
          {assetKind === 'dialogue' && <p className="help registry-hint">Thoại cho P3: <code>{'{"character_id":"CAPTAIN","text":"Lời thoại đã duyệt","duration_frames":36}'}</code>. Voice và timing dùng phiên bản ID này.</p>}
          <div className="form-row"><label className="field grow">Khung xương cần tìm<input value={motionSkeleton} onChange={e => setMotionSkeleton(e.target.value)} placeholder="SK_CAPTAIN_V1" /></label><label className="field grow">Nhãn chuyển động, cách nhau bằng dấu phẩy<input value={motionTags} onChange={e => setMotionTags(e.target.value)} placeholder="walk, urgent" /></label><button disabled={!motionSkeleton.trim() || !motionTags.trim()} onClick={searchMotion}>Tìm hoạt ảnh</button></div>
          {motionMatch && <p className="preflight ready">Clip tương thích duy nhất: <strong>{motionMatch.registry_id}</strong> · v{motionMatch.version}. Dùng ID này trong manifest rồi duyệt cảnh quay.</p>}
          {assets.length ? <div className="table-wrap"><table><thead><tr><th>ID</th><th>Loại</th><th>Phiên bản</th><th>Thông tin kỹ thuật</th></tr></thead><tbody>{assets.map(a => <tr key={a.id}><td className="strong">{a.registry_id}</td><td>{assetKindLabel(a.kind)}</td><td>v{a.version}</td><td><code>{JSON.stringify(a.metadata)}</code></td></tr>)}</tbody></table></div> : <Empty title="Thư viện chưa có tài nguyên" detail="Thêm ID để manifest có thể tham chiếu tài nguyên hợp lệ." />}</> : <Empty title="Chọn dự án để xem thư viện" detail="Dữ liệu thư viện được tách riêng theo từng dự án." />}
      </section>}

      {legacy('planner') && project?.workflow_version === 'V4_5' && <div className="simulation-note"><span className="mode-dot" /><p><strong>Dự án workflow V4.5.</strong> Chia scene/shot và chốt breakdown thực hiện ở tab Tập phim. Trang này là bộ lập kế hoạch V1, giữ lại cho dự án cũ.</p></div>}
      {legacy('planner') && <PlannerPage projectId={projectId} episodes={episodes} onApplied={async () => { await refresh(projectId) }} />}

      {legacy('shots') && <div className="layout-two shots-layout"><section className="panel"><div className="panel-heading"><div><h3>Tạo cảnh quay nháp</h3><p>Kiểm tra schema, ID và thời gian trước khi lưu.</p></div></div>
        {project ? <><label className="field">Tập phim<select value={newEpisodeId} onChange={e => setNewEpisodeId(e.target.value)}><option value="">Chọn tập</option>{episodes.map(e => <option key={e.id} value={e.id}>{e.name}</option>)}</select></label><label className="field">Manifest cảnh quay (JSON)<textarea className="manifest-editor" value={draftManifestText} onChange={e => setDraftManifestText(e.target.value)} rows={12} spellCheck={false} /></label><button className="button-primary" disabled={!newEpisodeId} onClick={createShot}>Tạo cảnh quay</button></> : <Empty title="Chưa chọn dự án" detail="Chọn dự án và tạo tập phim trước khi thêm cảnh quay." />}
      </section><section className="panel"><div className="panel-heading"><div><h3>Danh sách cảnh quay</h3><p>Chọn cảnh để xem phiên bản, khóa và tác động thay đổi.</p></div><span className="count">{shots.length} cảnh</span></div>
        {shots.length ? <div className="shot-list">{shots.map(s => <button className="shot-row" key={s.id} onClick={() => { setShotId(s.id); setLegacyTab('inspector') }}><div className="shot-main"><strong>{s.code}</strong><span>Phiên bản {s.current_revision} · khung hình {s.manifest.timeline.in}–{s.manifest.timeline.out_exclusive}</span></div><div className="shot-meta"><span>{routeLabel(s.manifest.route.suggested)}</span><Status value={s.approval ? 'APPROVED' : 'NEEDS_REVIEW'} />{s.states.some(x => x.validity === 'STALE') && <Status value="STALE" />}</div></button>)}</div> : <Empty title="Chưa có cảnh quay" detail="Các cảnh quay mới sẽ xuất hiện tại đây." />}
      </section></div>}

      {legacy('inspector') && <><ShotPicker shots={shots} value={shotId} onChange={setShotId} />{selected ? <div className="stack"><section className="panel"><div className="panel-heading"><div><h3>{selected.code} <span className="muted">· phiên bản {selected.current_revision}</span></h3><p>Thay đổi manifest sẽ tạo phiên bản mới, không ghi đè bản cũ.</p></div><Status value={selected.approval ? 'APPROVED' : 'NEEDS_REVIEW'} /></div><p className="hash-line">Mã băm đầu vào <code title={selected.input_hash}>{selected.input_hash}</code></p><label className="field">Manifest hiện hành<textarea className="manifest-editor" value={manifestText} onChange={e => setManifestText(e.target.value)} rows={12} spellCheck={false} /></label><div className="actions"><button onClick={() => previewEdit(false)}>Xem thay đổi & tác động</button><button className="button-primary" onClick={() => previewEdit(true)}>Lưu phiên bản mới</button></div>{preview && <div className="impact"><h4>Tác động dự kiến</h4><p>Trường thay đổi: {preview.changed_fields.length ? preview.changed_fields.join(', ') : 'Không có'}</p><p>Thành phần bị ảnh hưởng: {preview.affected_subsystems.length ? preview.affected_subsystems.map(subsystemLabel).join(', ') : 'Không có'}</p><p>Đầu ra sẽ cũ: {preview.stale_artifacts.length}</p>{preview.media_impact && <p>Video / bàn giao bị ảnh hưởng: {Object.entries(preview.media_impact).map(([name, count]) => `${name}: ${count}`).join(' · ')}</p>}</div>}</section>
        <section className="panel"><div className="panel-heading"><div><h3>Thành phần & khóa</h3><p>Chế độ, khóa, hiệu lực và duyệt được theo dõi riêng.</p></div></div><label className="field fingerprint-field">Dấu vân tay nội dung để tiếp nhận chỉnh sửa<input value={adoptFingerprint} onChange={e => setAdoptFingerprint(e.target.value)} placeholder="Nhập mã đã kiểm tra" /></label><div className="table-wrap"><table><thead><tr><th>Thành phần</th><th>Chế độ</th><th>Khóa</th><th>Hiệu lực</th><th>Duyệt</th><th>Thao tác</th></tr></thead><tbody>{selected.states.map(s => <tr key={s.subsystem}><td className="strong">{subsystemLabel(s.subsystem)}</td><td>{stateLabel(s.mode)}</td><td><Status value={s.lock_state} /></td><td><Status value={s.validity} /></td><td><Status value={s.review} /></td><td className="table-actions"><button onClick={() => changeState(s, 'MANUAL', 'LOCKED')}>Khóa thủ công</button><button onClick={() => changeState(s, 'AUTO', 'UNLOCKED')}>Bật tự động</button><button disabled={!adoptFingerprint.trim()} onClick={() => changeState(s, 'MANUAL', 'LOCKED', true)}>Tiếp nhận & khóa</button></td></tr>)}</tbody></table></div></section></div> : <Empty title="Chưa chọn cảnh quay" detail="Chọn một cảnh trong Bảng cảnh hoặc danh sách phía trên." />}</>}

      {legacy('review') && <><ShotPicker shots={shots} value={shotId} onChange={setShotId} />{selected ? <div className="layout-two"><section className="panel"><div className="panel-heading"><div><h3>Duyệt kế hoạch</h3><p>Quyết định gắn với đúng phiên bản {selected.current_revision} của {selected.code}.</p></div><Status value={selected.approval ? 'APPROVED' : 'NEEDS_REVIEW'} /></div><div className="info-line"><span>Tuyến đề xuất</span><strong>{routeLabel(selected.manifest.route.suggested)}</strong></div><label className="field">Tuyến được duyệt<select value={approvedRoute} onChange={e => setApprovedRoute(e.target.value)} disabled={!!selected.approval}>{routes.map(r => <option key={r} value={r}>{routeLabel(r)}</option>)}</select></label><button className="button-primary" disabled={!!selected.approval} onClick={() => act(() => api(`/shots/${selected.id}/approve`, { exact_revision: selected.current_revision, expected_current_revision: selected.current_revision, route: approvedRoute }), 'Đã duyệt đúng phiên bản kế hoạch.')}>Duyệt kế hoạch</button>{selected.approval && <p className="help">Đã duyệt tuyến {routeLabel(selected.approval.decision.approved_route)}.</p>}</section><section className="panel"><div className="panel-heading"><div><h3>Duyệt thành phần</h3><p>Mỗi quyết định gắn với phiên bản trạng thái hiện hành.</p></div></div><div className="review-list">{selected.states.map(s => <div key={s.subsystem} className="review-row"><div><strong>{subsystemLabel(s.subsystem)}</strong><span><Status value={s.validity} /> <Status value={s.review} /></span></div><button disabled={s.review === 'APPROVED'} onClick={() => act(() => api(`/shots/${selected.id}/subsystems/${s.subsystem}/approve`, { exact_shot_revision: selected.current_revision, expected_state_revision: s.state_revision }), `Đã duyệt ${subsystemLabel(s.subsystem).toLowerCase()}.`)}>Duyệt</button></div>)}</div></section></div> : <Empty title="Chưa chọn cảnh quay" detail="Chọn cảnh để duyệt kế hoạch và các thành phần." />}</>}

      {legacy('voice') && project?.workflow_version === 'V4_5' && <div className="simulation-note"><span className="mode-dot" /><p><strong>Dự án workflow V4.5.</strong> Thoại ElevenLabs và timing (C4) thực hiện ở tab Tập phim. Trang này là luồng voice thử của V1, giữ lại cho dự án cũ.</p></div>}
      {legacy('voice') && <>
        <div className="simulation-note"><span className="mode-dot" /><p><strong>Giọng thử local.</strong> WAV là âm mẫu tổng hợp, không phải lời thoại, không thể dùng để duyệt chất lượng lip sync. Provider live và thao tác UE đang bị chặn.</p></div>
        <ShotPicker shots={shots} value={shotId} onChange={setShotId} />
        {selected ? <div className="stack">
          <section className="panel"><div className="panel-heading"><div><h3>Tạo và duyệt voice take</h3><p>Performance phải có ID, dialogue và voice profile trong manifest. Mỗi lần tạo mới sinh take version riêng.</p></div><span className="count">{voiceTakes.length} take</span></div>
            <div className="form-row"><label className="field grow">Performance có thoại<select value={voicePerformanceId} onChange={e => setVoicePerformanceId(e.target.value)}><option value="">Chọn performance</option>{selected.manifest.performances?.filter((p: Record<string, unknown>) => p.voice_profile_id).map((p: Record<string, string>) => <option key={p.performance_id} value={p.performance_id}>{p.performance_id} · {p.character_id} · {p.dialogue_id} · {p.voice_profile_id}</option>)}</select></label><button className="button-primary" disabled={!voicePerformanceId} onClick={() => { setTimingPreview(null); act(() => api(`/shots/${selected.id}/voice/jobs`, { performance_id: voicePerformanceId, client_request_id: crypto.randomUUID() }), 'Đã xếp việc tạo WAV thử; theo dõi trạng thái bên dưới.') }}>Tạo / tạo lại take thử</button></div>
            {voiceTakes.length ? <div className="table-wrap voice-table"><table><thead><tr><th>Performance</th><th>Take</th><th>WAV thử</th><th>Review</th><th>Khóa</th><th>Thao tác</th></tr></thead><tbody>{voiceTakes.map(t => <tr key={t.id}><td className="strong">{t.performance_id}</td><td>v{t.version} · {(t.sample_count / t.sample_rate).toFixed(3)} s<br /><small>{t.sample_rate} Hz · {t.provider_kind}</small></td><td><audio controls preload="none" src={`/api/voice/takes/${t.id}/wav`} aria-label={`Nghe take ${t.version}`} /></td><td><Status value={t.review} /> <Status value={t.validity} /></td><td><Status value={t.lock_state} /></td><td className="table-actions"><button disabled={t.review !== 'NEEDS_REVIEW'} onClick={() => { setVoiceLockPreview(null); act(() => api(`/voice/takes/${t.id}/review`, { expected_version: t.version, decision: 'APPROVED' }), 'Đã duyệt take đúng phiên bản.') }}>Duyệt</button><button disabled={t.review !== 'NEEDS_REVIEW'} onClick={() => { setVoiceLockPreview(null); act(() => api(`/voice/takes/${t.id}/review`, { expected_version: t.version, decision: 'REJECTED' }), 'Đã từ chối take.') }}>Từ chối</button><button disabled={t.review !== 'APPROVED' || t.lock_state === 'LOCKED'} onClick={() => previewVoiceLock(t.id)}>Xem tác động</button><button disabled={t.review !== 'APPROVED' || t.lock_state === 'LOCKED' || voiceLockPreview?.take_id !== t.id || !voiceLockPreview.allowed} onClick={() => { setTimingPreview(null); setVoiceLockPreview(null); act(() => api(`/voice/takes/${t.id}/lock`, { expected_version: t.version, expected_selection_revision: voiceLockPreview?.selection_revision }), 'Đã khóa voice take; face/audio và đầu ra liên quan được đánh dấu cũ.') }}>Khóa voice</button></td></tr>)}</tbody></table></div> : <Empty title="Chưa có take" detail="Chọn performance và tạo WAV thử để kiểm tra luồng phiên bản." />}
            {voiceLockPreview && <div className={`preflight ${voiceLockPreview.overflow_frames ? 'blocked' : 'ready'} settings-result`}>Take v{voiceLockPreview.take_version} · {voiceLockPreview.duration_frames} frame. {voiceLockPreview.affected_subsystems.length ? `Sẽ đánh dấu cũ: ${voiceLockPreview.affected_subsystems.map(subsystemLabel).join(', ')}; ${voiceLockPreview.stale_artifacts.length} đầu ra.` : 'Đang là take đã chọn.'} {voiceLockPreview.overflow_frames > 0 && `Thoại vượt shot ${voiceLockPreview.overflow_frames} frame; sau khi khóa phải chỉnh timing/shot.`}</div>}
            {voiceJobs.filter(j => j.shot_id === selected.id).length > 0 && <div className="voice-job-list">{voiceJobs.filter(j => j.shot_id === selected.id).map(j => <div key={j.id}><strong>{j.performance_id}</strong> · <Status value={j.state} /> · lần {j.attempt} {j.error && <small>{errorLabel(j.error)}</small>}{j.state === 'RECONCILING' && <button onClick={() => act(() => api(`/voice/jobs/${j.id}/reconcile`, {}), 'Đã đối soát WAV thử.')}>Đối soát</button>}{j.state === 'BLOCKED' && <button onClick={() => act(() => api(`/voice/jobs/${j.id}/retry`, {}), 'Đã xếp lại job voice bị chặn trước khi ghi.')}>Thử lại</button>}</div>)}</div>}
          </section>
          <section className="panel"><div className="panel-heading"><div><h3>Nhịp thoại từ voice đã khóa</h3><p>Frame sử dụng ceil(samples × FPS.num / (sample_rate × FPS.den)); khoảng [in, out). Không tự cắt hoặc kéo giãn WAV.</p></div></div>
            <div className="actions"><button onClick={previewVoiceTiming}>Xem vị trí & tác động</button><button className="button-primary" disabled={!timingPreview?.allowed} onClick={() => act(() => api(`/shots/${selected.id}/timing`, { expected_shot_revision: timingPreview?.shot_revision, expected_input_hash: timingPreview?.input_hash }), 'Đã tạo timing revision; cần duyệt đúng phiên bản.')}>Tạo timing revision</button></div>
            {timingPreview && <div className={`preflight ${timingPreview.allowed ? 'ready' : 'blocked'} settings-result`}>{timingPreview.blockers.length ? timingPreview.blockers.join(' · ') : timingPreview.placements.map(p => `${p.performance_id}: local [${p.start_local_frame}, ${p.end_local_exclusive}), tập [${p.start_episode_frame}, ${p.end_episode_exclusive}) · voice v${p.voice_version}`).join(' · ')}</div>}
            {timings.length > 0 && <div className="table-wrap settings-result"><table><thead><tr><th>Timing</th><th>Shot revision</th><th>Hiệu lực</th><th>Duyệt</th><th>Vị trí</th><th></th></tr></thead><tbody>{timings.map(t => <tr key={t.id}><td>v{t.version}</td><td>v{t.shot_revision}</td><td><Status value={t.validity} /></td><td><Status value={t.review} /></td><td>{t.placements.map(p => `${p.performance_id}: ${p.start_episode_frame}–${p.end_episode_exclusive}`).join(' · ')}</td><td><button disabled={t.review !== 'NEEDS_REVIEW' || t.validity !== 'CURRENT'} onClick={() => act(() => api(`/timing/${t.id}/approve`, { expected_version: t.version }), 'Đã duyệt timing đúng phiên bản.')}>Duyệt timing</button></td></tr>)}</tbody></table></div>}
          </section>
          <div className="layout-two"><section className="panel"><div className="panel-heading"><div><h3>Gương mặt & âm thanh UE</h3><p>Kế hoạch gắn performance, MetaHuman và voice revision. Solve/bake/track cần plugin P0 đã kiểm chứng.</p></div></div><div className="actions"><button onClick={() => showP3Plan('facial')}>Xem kế hoạch</button><button onClick={() => act(() => api(`/shots/${selected.id}/facial/preflight`, {}), 'Preflight facial hoàn tất.')}>Kiểm tra UE</button></div>{facialPlan && <div className="preflight blocked settings-result">{facialPlan.placements.map(p => `${p.performance_id}: frame ${p.start_episode_frame}`).join(' · ')} · Chưa có API facial được kiểm chứng trong UE.</div>}<p className="help">Vị trí đúng frame và chất lượng lip sync là hai lần duyệt riêng. Chưa có nút duyệt chất lượng khi chưa có solve thật.</p></section>
          <section className="panel"><div className="panel-heading"><div><h3>Máy quay</h3><p>Ba ý đồ hỗ trợ ở mức kế hoạch: tĩnh khi thoại, theo nhân vật, hai nhân vật.</p></div></div><div className="actions"><button onClick={() => showP3Plan('camera')}>Xem kế hoạch</button><button onClick={() => act(() => api(`/shots/${selected.id}/camera/preflight`, {}), 'Preflight camera hoàn tất.')}>Kiểm tra UE</button></div>{cameraPlan && <div className="preflight blocked settings-result">{cameraPlan.mode} · {cameraPlan.subjects.join(', ')} · Chưa có property Black Eye được kiểm chứng.</div>}<p className="help">Camera thủ công/đã khóa hoặc có fingerprint sửa ngoài app sẽ bị chặn.</p></section></div>
        </div> : <Empty title="Chưa chọn cảnh" detail="Chọn một cảnh có thoại và voice profile để bắt đầu." />}
      </>}

      {legacy('cache') && <><ShotPicker shots={shots} value={shotId} onChange={setShotId} />{selected ? <CachePage key={selected.id} shot={selected} projectId={projectId} assets={assets} artifacts={artifacts} /> : <Empty title="Chưa chọn cảnh" detail="Chọn một cảnh để cấu hình simulation và xem guard render." />}</>}
      {legacy('media') && <MediaPage projectId={projectId} episodes={episodes} shots={shots} assets={assets} />}

      {legacy('jobs') && <>
        {gpuQueue && <div className="simulation-note"><span className="mode-dot" /><p><strong>Hàng đợi GPU dùng chung.</strong> {gpuQueue.lease ? `Đang giữ: ${gpuOwnerLabel(gpuQueue.lease.owner_kind)}${gpuQueue.lease.state === 'RECONCILING' ? ' · cần đối soát trước khi việc khác chạy' : ''}.` : 'Đang rảnh.'} Chờ: {gpuQueue.waiting.unreal} lắp dựng Unreal · {gpuQueue.waiting.scans} quét asset · {gpuQueue.waiting.foley} Foley · {gpuQueue.waiting.jobs} việc khác. Render UE, quét asset, Kimodo và Foley không chạy song song trên cùng GPU.</p></div>}
        <section className="panel job-controls">
          <div><h3>Lắp dựng Unreal · P2</h3><p>Chỉ chạy trên máy có UE 5.8, project và asset thật. Chọn 3–5 cảnh đã duyệt tuyến Unreal.</p></div>
          <label className="field">Tập phim<select value={unrealEpisodeId} onChange={e => { setUnrealEpisodeId(e.target.value); setUnrealShotCodes(shots.filter(s => s.episode_id === e.target.value).slice(0, 4).map(s => s.code).join(', ')); setUnrealPreview(null); setUnrealPreflight(false) }}><option value="">Chọn tập</option>{episodes.map(e => <option key={e.id} value={e.id}>{e.name}</option>)}</select></label>
          <label className="field">Mã 3–5 cảnh, cách nhau bằng dấu phẩy<input value={unrealShotCodes} onChange={e => { setUnrealShotCodes(e.target.value); setUnrealPreview(null); setUnrealPreflight(false) }} /></label>
          <div className="form-row"><button disabled={!unrealEpisodeId} onClick={() => checkUnreal(false)}>Xem kế hoạch</button><button disabled={!unrealPreview} onClick={() => checkUnreal(true)}>Kiểm tra trong Unreal</button><button className="button-primary" disabled={!unrealPreflight || !unrealPreview} onClick={() => act(() => api(`/projects/${projectId}/episodes/${unrealEpisodeId}/unreal/build`, { shot_codes: unrealShotCodes.split(',').map(s => s.trim()).filter(Boolean) }), 'Đã xếp công việc Unreal. Theo dõi biên nhận bên dưới.')}>Lắp dựng 3–5 cảnh</button></div>
          {unrealPreview && <div className="preflight ready"><strong>{unrealPreview.master_path}</strong> · {unrealPreview.shots.length} cảnh · {unrealPreview.shots.map(s => `${s.shot_code} [${s.timeline.in}, ${s.timeline.out_exclusive})`).join(' · ')}</div>}
          {unrealPreflight && <div className="preflight ready">{unrealDiff.length ? <><strong>{unrealDiff.length} thay đổi do app quản lý:</strong> {unrealDiff.map(c => `${c.action} ${c.character_id ?? c.path}`).join(' · ')}</> : 'Không có thay đổi trong phần do app quản lý.'}</div>}
          <label className="acceptance-confirm"><input type="checkbox" checked={unrealWorkerStopped} onChange={e => setUnrealWorkerStopped(e.target.checked)} />Tôi đã dừng Unreal worker cũ trước khi đối soát.</label>
          {unrealJobs.length ? <div className="table-wrap"><table><thead><tr><th>Tập</th><th>Trạng thái</th><th>Lần chạy</th><th>Mã thao tác</th><th>Đối soát</th></tr></thead><tbody>{unrealJobs.map(j => <tr key={j.id}><td>{episodes.find(e => e.id === j.episode_id)?.name ?? '—'}</td><td><Status value={j.state} />{j.error && <small className="row-error">{errorLabel(j.error)}</small>}</td><td>{j.attempt}</td><td><code title={j.operation_id}>{j.operation_id.slice(0, 12)}…</code></td><td className="table-actions"><button disabled={j.state !== 'BLOCKED'} onClick={() => act(() => api(`/unreal/jobs/${j.id}/retry`, {}), 'Đã xếp lại việc bị chặn trước khi ghi.')}>Thử lại</button><button disabled={j.state !== 'RECONCILING' || !unrealWorkerStopped} onClick={() => act(async () => { const result = await api(`/unreal/jobs/${j.id}/reconcile`, { worker_stopped: unrealWorkerStopped }); setUnrealWorkerStopped(false); return result }, 'Đã kiểm tra lại đầu ra Unreal.')}>Đối soát</button></td></tr>)}</tbody></table></div> : <p>Chưa có công việc Unreal.</p>}
        </section>
        <div className="simulation-note"><span className="mode-dot" /><p><strong>Hàng đợi mô phỏng.</strong> Công việc chạy trên máy để kiểm tra điều phối. Biên nhận không chứng minh việc ghi tài nguyên Unreal.</p></div><ShotPicker shots={shots} value={shotId} onChange={setShotId} />{selected && <section className="panel job-controls"><div><h3>Tạo công việc thử</h3><p>{selected.code} · phiên bản {selected.current_revision}</p></div><label className="field">Thành phần<select value={buildSubsystem} onChange={e => { setBuildSubsystem(e.target.value); setPreflight(null) }}>{selected.states.map(s => <option key={s.subsystem} value={s.subsystem}>{subsystemLabel(s.subsystem)}</option>)}</select></label><button onClick={checkPreflight}>Kiểm tra điều kiện</button><button className="button-primary" onClick={() => act(() => api(`/shots/${selected.id}/build`, { revision: selected.current_revision, subsystem: buildSubsystem, profile_version: 'P1_FAKE_V1', resource: 'CPU' }), 'Đã xếp công việc mô phỏng.')}>Xếp việc mô phỏng</button>{preflight && <div className={`preflight ${preflight.allowed ? 'ready' : 'blocked'}`}>{preflight.allowed ? 'Đủ điều kiện để xếp công việc mô phỏng.' : `Bị chặn: ${preflight.blockers.map(errorLabel).join('; ')}`}</div>}</section>}
        <section className="panel"><div className="panel-heading"><div><h3>Hàng đợi & khôi phục</h3><p>Trạng thái được lưu và có thể xem lại sau khi mở app.</p></div><span className="count">{jobs.length} công việc</span></div>{jobs.length ? <div className="table-wrap"><table><thead><tr><th>Cảnh quay</th><th>Thành phần</th><th>Trạng thái</th><th>Bộ thực thi</th><th>Mã thao tác</th><th>Khôi phục</th></tr></thead><tbody>{jobs.map(j => <tr key={j.id}><td className="strong">{shots.find(s => s.id === j.shot_id)?.code ?? '—'}</td><td>{subsystemLabel(j.subsystem)}</td><td><Status value={j.state} />{j.error && <small className="row-error">{errorLabel(j.error)}</small>}</td><td>{adapterLabel(j.adapter_kind)}</td><td><code title={j.operation_id}>{j.operation_id.slice(0, 12)}…</code></td><td className="table-actions"><button disabled={j.state !== 'BLOCKED'} onClick={() => act(() => api(`/jobs/${j.id}/retry`, {}), 'Đã yêu cầu thử lại.')}>Thử lại</button><button disabled={j.state !== 'RECONCILING'} onClick={() => act(() => api(`/jobs/${j.id}/reconcile`, {}), 'Đã đối soát công việc.')}>Đối soát</button></td></tr>)}</tbody></table></div> : <Empty title={project ? 'Chưa có công việc' : 'Chưa chọn dự án'} detail={project ? 'Chọn cảnh, kiểm tra điều kiện rồi xếp một công việc mô phỏng.' : 'Chọn dự án để xem hàng đợi và lịch sử xử lý.'} />}</section>
        <section className="panel"><div className="panel-heading"><div><h3>Lịch sử đầu ra</h3><p>Đầu ra cũ được giữ lại và đánh dấu khi đầu vào thay đổi.</p></div><span className="count">{artifacts.length} bản</span></div>{artifacts.length ? <div className="table-wrap"><table><thead><tr><th>Cảnh quay</th><th>Vai trò</th><th>Phiên bản</th><th>Hiệu lực</th><th>Nguồn tạo</th></tr></thead><tbody>{artifacts.map(a => <tr key={a.id}><td className="strong">{shots.find(s => s.id === a.shot_id)?.code ?? '—'}</td><td>{subsystemLabel(a.role)}</td><td>v{a.version}</td><td><Status value={a.validity} /></td><td>{adapterLabel(a.provenance)}</td></tr>)}</tbody></table></div> : <Empty title="Chưa có đầu ra" detail="Lịch sử đầu ra sẽ xuất hiện sau khi công việc hoàn tất." />}</section>
      </>}
    </main>
  </div>
}
