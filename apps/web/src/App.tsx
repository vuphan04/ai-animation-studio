import { useCallback, useEffect, useState } from 'react'
import { api, bootstrap, type Episode, type FlowState, type GpuQueue, type Progress, type Project } from './api'
import { errorLabel } from './uiText'
import { lang, setLanguage, t } from './i18n'
import AISettingsPanel from './AISettingsPanel'
import BackupPanel from './BackupPanel'
import ClaudeCodePanel from './ClaudeCodePanel'
import EpisodePage from './EpisodePage'
import FinalsPage from './FinalsPage'
import FoleyToolPanel from './FoleyToolPanel'
import HomePage from './HomePage'
import ProductionPage from './ProductionPage'
import SeriesPage from './SeriesPage'
import StepTabs, { type StepDef } from './StepTabs'
import ToolSettingsPanel from './ToolSettingsPanel'
import UnrealSettingsPanel from './UnrealSettingsPanel'
import VideoModelSettingsPanel from './VideoModelSettingsPanel'
import VoiceSettingsPanel from './VoiceSettingsPanel'
import WorkflowBar from './WorkflowBar'

type Page = 'home' | 'series' | 'episode' | 'production' | 'finals' | 'system' | 'settings'
type Theme = 'day' | 'night'
type View = { page: Page; steps: Record<string, string> }

// The four zones of the workflow diagram come first, in the order an episode moves through them.
const pages: { id: Page; zone?: string; label: string; description: string }[] = [
  { id: 'home', label: t('Tổng quan'), description: t('Dự án, các tập và bản đồ workflow: việc đã duyệt và bước tiếp theo') },
  { id: 'series', zone: '0', label: t('Thư viện series'), description: t('Chuẩn kỹ thuật và ngân sách (S1), nhân vật và bối cảnh (S2), quét asset và mapping (S3, CS1), Style bible (S4, CS2)') },
  { id: 'episode', zone: 'A', label: t('Tập phim'), description: t('Kịch bản và breakdown (C1), thư viện của tập (C2), phương thức và chi phí (C3), thoại và timing (C4)') },
  { id: 'production', zone: 'B', label: t('Dựng shot'), description: t('Nhánh UE (C5), cache và proxy (C6), nhánh AI và Hybrid, rough cut và khóa dựng (C7)') },
  { id: 'finals', zone: 'C', label: t('Final & xuất phim'), description: t('Final và chuẩn hóa (C8), Foley và âm thanh (C9), gói timeline, QC và xuất phim (C10)') },
  { id: 'system', label: t('Hệ thống'), description: t('Hàng đợi GPU dùng chung, sao lưu và phục hồi dữ liệu') },
  { id: 'settings', label: t('Cài đặt'), description: t('Kết nối Unreal, dịch vụ AI, giọng thoại, model video, FFmpeg và Foley trên máy này') }
]
const workflowPages = ['series', 'episode', 'production', 'finals']
const settingsSteps: StepDef[] = [{ id: 'unreal', label: t('Kết nối Unreal') }, { id: 'ai', label: t('Dịch vụ AI & giọng thoại') }, { id: 'video', label: t('Model video AI') }, { id: 'tools', label: t('Công cụ xử lý video & Foley local') }]
const firstSteps: Record<string, string> = { series: 's1', episode: 'breakdown', production: 'ue', finals: 'final', settings: 'unreal' }
const gpuOwnerLabel = (kind: string) => ({ UNREAL: t('dựng trong Unreal'), SCAN: t('quét asset hoặc chụp reference trong Unreal'), FOLEY: t('tạo Foley'), MRQ: 'render Movie Render Queue', KIMODO: 'Kimodo' }[kind] ?? kind)
const stored = (key: string) => { try { return localStorage.getItem(key) } catch { return null } }
const remember = (key: string, value: string) => { try { localStorage.setItem(key, value) } catch { /* Session-only when browser storage is unavailable. */ } }

// The open screen survives a reload of the page, which is also how the language changes.
function savedView(): View {
  try {
    const view = JSON.parse(sessionStorage.getItem('studio_view') ?? 'null') as View | null
    if (view && pages.some(p => p.id === view.page)) return { page: view.page, steps: { ...firstSteps, ...view.steps } }
  } catch { /* Start on the overview. */ }
  return { page: 'home', steps: firstSteps }
}

const icon = (path: string) => <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={path} /></svg>
const icons = {
  reload: icon('M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7'),
  moon: icon('M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z'),
  sun: icon('M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z')
}

export default function App() {
  const [view, setView] = useState<View>(savedView)
  const [theme, setTheme] = useState<Theme>(() => document.documentElement.dataset.theme === 'day' ? 'day' : 'night')
  const [ready, setReady] = useState(false)
  const [serverBuild, setServerBuild] = useState<string | null>(null)
  const [unrealConfigured, setUnrealConfigured] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [projects, setProjects] = useState<Project[]>([])
  const [projectId, setProjectId] = useState('')
  const [episodes, setEpisodes] = useState<Episode[]>([])
  const [episodeId, setEpisodeId] = useState('')
  const [progress, setProgress] = useState<Progress | null>(null)
  const [flowVersion, setFlowVersion] = useState(0)
  const [gpuQueue, setGpuQueue] = useState<GpuQueue | null>(null)

  const { page, steps } = view
  const project = projects.find(p => p.id === projectId)
  const activePage = pages.find(p => p.id === page)!
  const go = (target: string, step?: string) => { setView(current => ({ page: target as Page, steps: step ? { ...current.steps, [target]: step } : current.steps })); window.scrollTo({ top: 0 }) }
  const openProject = (id: string) => { if (id === projectId) return; setProjectId(id); setEpisodeId(''); setEpisodes([]); setProgress(null) }
  const setStep = (target: string) => (step: string) => setView(current => ({ ...current, steps: { ...current.steps, [target]: step } }))
  const marks = (target: string): Record<string, FlowState> => Object.fromEntries((progress?.zones.find(z => z.page === target)?.checkpoints ?? []).map(c => [c.step, c.state]))

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    remember('studio_theme', theme)
  }, [theme])
  useEffect(() => { try { sessionStorage.setItem('studio_view', JSON.stringify(view)) } catch { /* The view simply starts over next time. */ } }, [view])

  const refresh = useCallback(async (pid = projectId) => {
    const [all, gpu] = await Promise.all([api<Project[]>('/projects'), api<GpuQueue>('/resources/gpu-queue')])
    setProjects(all); setGpuQueue(gpu)
    setEpisodes(pid && all.some(p => p.id === pid) ? await api<Episode[]>(`/projects/${pid}/episodes`) : [])
    return all
  }, [projectId])

  useEffect(() => {
    bootstrap().then(async session => {
      setServerBuild(session.build_id); setUnrealConfigured(session.unreal_configured); setReady(true)
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
  }, [projectId, ready])

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

  useEffect(() => {
    if (!projectId || !ready) { setProgress(null); return }
    let live = true
    api<Progress>(`/projects/${projectId}/progress${episodeId ? `?episode_id=${episodeId}` : ''}`).then(value => { if (live) setProgress(value) }).catch(() => undefined)
    return () => { live = false }
  }, [projectId, episodeId, flowVersion, page, steps, ready])

  async function act(action: () => Promise<unknown>, message: string, pid = projectId) {
    setError(''); setNotice('')
    try {
      await action()
      await refresh(pid)
      setNotice(message)
    } catch (e) { setError(errorLabel(e)) }
  }
  const createProject = (name: string) => act(async () => { const created = await api<Project>('/projects', { name }); openProject(created.id) }, t('Đã tạo dự án. Thêm tập đầu tiên rồi bắt đầu ở Khu 0 · Thư viện series.'), '')
  const addEpisode = (name: string) => act(async () => { const created = await api<Episode>(`/projects/${projectId}/episodes`, { name, ordinal: Math.max(0, ...episodes.map(e => e.ordinal)) + 1 }); setEpisodeId(created.id) }, t('Đã thêm tập và chọn làm tập đang làm.'))

  if (!ready) return <main className="boot">
    <div className="brand-mark">A</div><h1>AI Animation Studio</h1>
    {error ? <><p className="alert error" role="alert">{error}</p><button onClick={() => window.location.reload()}>{t('Thử lại')}</button></> : <p>{t('Đang mở phiên làm việc trên máy…')}</p>}
  </main>

  return <div className="shell">
    <header className="topbar">
      <button type="button" className="brand" title={t('Tổng quan')} onClick={() => go('home')}><span className="brand-mark" aria-hidden="true">A</span><span className="brand-name">AI Animation Studio</span></button>
      <div className="workspace">
        <label className="workspace-pick project-select"><span>{t('Dự án')}</span>
          <select value={projectId} onChange={e => { openProject(e.target.value); setError(''); setNotice('') }}>
            <option value="">{t('Chọn dự án')}</option>
            {projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
        {project && <label className="workspace-pick episode-select"><span>{t('Tập')}</span>
          <select value={episodeId} onChange={e => setEpisodeId(e.target.value)}>
            <option value="">{episodes.length ? t('Chọn tập') : t('Chưa có tập')}</option>
            {episodes.map(e => <option key={e.id} value={e.id}>{e.name}</option>)}
          </select>
        </label>}
        <button type="button" className="icon-button" title={t('Tải lại')} aria-label={t('Tải lại')} onClick={() => refresh().then(() => setFlowVersion(v => v + 1)).catch(e => setError(errorLabel(e)))}>{icons.reload}</button>
        {project && progress && <span className="workspace-stats" title={t('Checkpoint đã duyệt của tập đang làm')}><strong>{progress.done}/{progress.total}</strong> checkpoint</span>}
      </div>
      <div className="topbar-actions">
        <button type="button" className={`unreal-chip ${unrealConfigured ? 'set' : ''}`} title={unrealConfigured ? t('Đã lưu đường dẫn Unreal. Cần kiểm chứng trên project và asset thật.') : t('Chọn Unreal Editor và project để dùng các bước Unreal')} onClick={() => go('settings', 'unreal')}><span className="mode-dot" />Unreal · {unrealConfigured ? t('đã cấu hình') : t('chưa cấu hình')}</button>
        <div className="segmented" role="group" aria-label={t('Ngôn ngữ giao diện')}>
          <button type="button" aria-pressed={lang === 'vi'} title={t('Tiếng Việt')} onClick={() => setLanguage('vi')}>VI</button>
          <button type="button" aria-pressed={lang === 'en'} title="English" onClick={() => setLanguage('en')}>EN</button>
        </div>
        <button type="button" className="icon-button theme-toggle" role="switch" aria-checked={theme === 'night'} aria-label={t('Chế độ ban đêm')} title={theme === 'night' ? t('Bật chế độ ban ngày') : t('Bật chế độ ban đêm')} onClick={() => setTheme(current => current === 'night' ? 'day' : 'night')}>{theme === 'night' ? icons.moon : icons.sun}</button>
      </div>
    </header>

    <nav className="tabs" aria-label={t('Các khu vực làm việc')}>
      {pages.map(p => <button key={p.id} type="button" className={`${page === p.id ? 'active' : ''} ${p.id === 'system' ? 'tab-gap' : ''}`} aria-current={page === p.id ? 'page' : undefined} title={p.description} onClick={() => go(p.id)}>{p.zone && <span className="tab-zone" title={t('Khu {0}', p.zone)}>{p.zone}</span>}{p.label}</button>)}
    </nav>

    {error && <div className="alert error" role="alert">{error}</div>}
    {notice && <div className="alert notice" role="status">{notice}</div>}
    {workflowPages.includes(page) && <WorkflowBar progress={progress} page={page} gpu={gpuQueue?.lease ? t('GPU đang bận: {0}', gpuOwnerLabel(gpuQueue.lease.owner_kind)) : null} onGo={go} />}

    <main className="content">
      <div className="page-heading">{activePage.zone && <span className="zone-tag">{t('Khu {0}', activePage.zone)}</span>}<h2>{activePage.label}</h2><p>{activePage.description}</p></div>

      {page === 'home' && <HomePage project={project} episodes={episodes} episodeId={episodeId} progress={progress} onCreateProject={createProject} onAddEpisode={addEpisode} onSelectEpisode={setEpisodeId} onGo={go} />}
      {page === 'series' && <SeriesPage projectId={projectId} episodes={episodes} episodeId={episodeId} unrealConfigured={unrealConfigured} step={steps.series} onStep={setStep('series')} marks={marks('series')} />}
      {page === 'episode' && <EpisodePage projectId={projectId} episodeId={episodeId} unrealConfigured={unrealConfigured} step={steps.episode} onStep={setStep('episode')} marks={marks('episode')} onOpenSeries={() => go('series', 'mapping')} />}
      {page === 'production' && <ProductionPage projectId={projectId} episodeId={episodeId} step={steps.production} onStep={setStep('production')} marks={marks('production')} />}
      {page === 'finals' && <FinalsPage projectId={projectId} episodeId={episodeId} step={steps.finals} onStep={setStep('finals')} marks={marks('finals')} />}

      {page === 'system' && <div className="stack">
        <section className="panel"><div className="panel-heading"><div><h3>{t('Hàng đợi GPU dùng chung')}</h3><p>{t('Dựng và render trong Unreal, quét asset và Foley không chạy song song trên cùng GPU; việc đến sau chờ việc trước xong.')}</p></div><span className={`status ${gpuQueue?.lease ? (gpuQueue.lease.state === 'RECONCILING' ? 'warning' : 'neutral') : 'success'}`}>{gpuQueue?.lease ? t('Đang giữ: {0}', gpuOwnerLabel(gpuQueue.lease.owner_kind)) : t('Đang rảnh')}</span></div>
          {gpuQueue && <dl className="stat-row">
            <div><dt>{t('Dựng Unreal')}</dt><dd>{gpuQueue.waiting.unreal}</dd></div>
            <div><dt>Render</dt><dd>{gpuQueue.waiting.renders}</dd></div>
            <div><dt>{t('Quét asset, chụp reference')}</dt><dd>{gpuQueue.waiting.scans}</dd></div>
            <div><dt>Foley</dt><dd>{gpuQueue.waiting.foley}</dd></div>
          </dl>}
          <p className="help">{t('Số việc đang chờ ở từng loại.')}{gpuQueue?.lease?.state === 'RECONCILING' ? t(' Lần chạy trước bị gián đoạn: mở đúng bước đang báo gián đoạn (dựng Unreal, render proxy hoặc final, quét asset, Foley) và xác nhận ở đó để giải phóng GPU.') : ''}</p>
        </section>
        <BackupPanel />
      </div>}

      {page === 'settings' && <div className="stack">
        <StepTabs steps={settingsSteps} value={steps.settings} onChange={setStep('settings')} label={t('Nhóm cài đặt')} />
        <div className="settings-layout" hidden={steps.settings !== 'unreal'}><UnrealSettingsPanel serverBuild={serverBuild} onConfigured={setUnrealConfigured} onServerBuild={setServerBuild} /></div>
        <div className="stack" hidden={steps.settings !== 'ai'}><AISettingsPanel /><ClaudeCodePanel /><VoiceSettingsPanel /></div>
        <div className="stack" hidden={steps.settings !== 'video'}><VideoModelSettingsPanel /></div>
        <div className="stack" hidden={steps.settings !== 'tools'}><ToolSettingsPanel /><FoleyToolPanel /></div>
      </div>}
    </main>
  </div>
}
