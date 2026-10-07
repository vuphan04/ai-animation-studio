import { useState } from 'react'
import type { Episode, Progress, Project } from './api'
import { flowSign, flowText } from './WorkflowBar'
import { t } from './i18n'

/** Project, its episodes and the workflow map: what is approved and what comes next. */
export default function HomePage({ project, episodes, episodeId, progress, onCreateProject, onAddEpisode, onSelectEpisode, onGo }: {
  project: Project | undefined; episodes: Episode[]; episodeId: string; progress: Progress | null
  onCreateProject: (name: string) => Promise<void>; onAddEpisode: (name: string) => Promise<void>; onSelectEpisode: (id: string) => void; onGo: (page: string, step?: string) => void
}) {
  const [projectName, setProjectName] = useState('')
  const [episodeName, setEpisodeName] = useState('')
  const episode = episodes.find(e => e.id === episodeId)
  const next = progress?.next ?? null

  return <div className="stack v45 home-page">
    <div className="layout-two">
      <section className="panel"><div className="panel-heading"><div><h3>{t('Dự án')}</h3><p>{project ? t('Dự án đang làm chọn ở thanh phía trên. Dữ liệu được giữ sau khi khởi động lại.') : t('Tạo dự án mới, hoặc chọn một dự án ở thanh phía trên.')}</p></div></div>
        {project && <p className="path-note"><strong>{project.name}</strong>{t(' · thư mục dữ liệu: ')}<code>{project.root}</code></p>}
        <div className="form-row"><label className="field grow">{t('Tạo dự án mới — tên dự án')}<input value={projectName} onChange={e => setProjectName(e.target.value)} placeholder={t('Ví dụ: Series Biển Đêm')} /></label><button className={project ? '' : 'button-primary'} disabled={!projectName.trim()} onClick={() => onCreateProject(projectName.trim()).then(() => setProjectName(''))}>{t('Tạo dự án')}</button></div>
      </section>
      <section className="panel"><div className="panel-heading"><div><h3>{t('Các tập')}</h3><p>{project ? t('Tập đang làm dùng chung cho các tab Tập phim, Dựng shot và Final & xuất phim.') : t('Chọn dự án để quản lý các tập.')}</p></div>{project && <span className="count">{episodes.length}{t(' tập')}</span>}</div>
        {project ? <>
          {episodes.length ? <ul className="episode-list">{episodes.map(e => <li key={e.id} className={e.id === episodeId ? 'current' : ''}><span className="index">{String(e.ordinal).padStart(2, '0')}</span><span className="episode-name">{e.name}</span>{e.id === episodeId ? <span className="status success">{t('Đang làm')}</span> : <button type="button" className="button-quiet" onClick={() => onSelectEpisode(e.id)}>{t('Làm tập này')}</button>}</li>)}</ul>
            : <p className="help">{t('Chưa có tập nào. Thêm tập đầu tiên để bắt đầu.')}</p>}
          <div className="form-row"><label className="field grow">{t('Thêm tập — tên tập')}<input value={episodeName} onChange={e => setEpisodeName(e.target.value)} placeholder={t('Ví dụ: Tập {0}', episodes.length + 1)} /></label><button className={episodes.length ? '' : 'button-primary'} disabled={!episodeName.trim()} onClick={() => onAddEpisode(episodeName.trim()).then(() => setEpisodeName(''))}>{t('Thêm tập')}</button></div>
        </> : <div className="empty"><span className="empty-icon" aria-hidden="true">◇</span><strong>{t('Chưa chọn dự án')}</strong><p>{t('Tạo dự án ở bên trái hoặc chọn dự án ở thanh phía trên.')}</p></div>}
      </section>
    </div>

    {project && progress && <section className="panel"><div className="panel-heading"><div><h3>{t('Bản đồ workflow')}{episode ? ` · ${episode.name}` : ''}</h3><p>{t('Bốn khu của sơ đồ workflow và các checkpoint bạn duyệt. Bấm vào một checkpoint để mở đúng bước.')}</p></div><span className={`status ${progress.total > 0 && progress.done === progress.total && progress.episode_id ? 'success' : 'neutral'}`}>{progress.done}/{progress.total}{t(' checkpoint đã duyệt')}</span></div>
      {next ? <div className="preflight ready settings-result flow-next-line"><span>{t('Bước tiếp theo: ')}<strong>{next.key} · {next.label}</strong>{next.detail ? ` — ${next.detail}` : ''}</span><button className="button-primary" onClick={() => onGo(next.page, next.step)}>{t('Làm bước này')}</button></div>
        : progress.episode_id ? <div className="preflight ready settings-result">{t('Tập này đã qua mọi checkpoint, từ CS1 đến C10.')}</div> : null}
      {!progress.episode_id && <p className="help">{episodes.length ? t('Chọn tập đang làm để thấy các checkpoint C1–C10 của tập.') : t('Thêm một tập để thấy các checkpoint C1–C10.')}</p>}
      <div className="zone-grid">{progress.zones.map(zone => <div key={zone.key} className="zone-card">
        <div className="zone-head"><span className="zone-tag">{t('Khu {0}', zone.key)}</span><strong>{zone.label}</strong><button type="button" className="button-quiet" onClick={() => onGo(zone.page)}>{t('Mở')}</button></div>
        {zone.checkpoints.length ? <ul>{zone.checkpoints.map(c => <li key={c.key}><button type="button" className={`zone-row ${c.state.toLowerCase()} ${next?.key === c.key ? 'next' : ''}`} onClick={() => onGo(c.page, c.step)}><span className="zone-sign" aria-hidden="true">{flowSign[c.state]}</span><span className="zone-key">{c.key}</span><span className="zone-label">{c.label}<small>{flowText[c.state]}{c.detail ? ` · ${c.detail}` : ''}</small></span></button></li>)}</ul>
          : <p className="help">{t('Các checkpoint của khu này tính theo từng tập.')}</p>}
      </div>)}</div>
    </section>}
  </div>
}
