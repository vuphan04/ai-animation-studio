import { useState } from 'react'
import type { Episode, Progress, Project } from './api'
import { flowSign, flowText } from './WorkflowBar'

/** Project, its episodes and the workflow map: what is approved and what comes next. */
export default function HomePage({ project, episodes, episodeId, progress, hasLegacyData, onCreateProject, onAddEpisode, onSelectEpisode, onUpgrade, onGo }: {
  project: Project | undefined; episodes: Episode[]; episodeId: string; progress: Progress | null; hasLegacyData: boolean
  onCreateProject: (name: string) => Promise<void>; onAddEpisode: (name: string) => Promise<void>; onSelectEpisode: (id: string) => void; onUpgrade: () => Promise<void>; onGo: (page: string, step?: string) => void
}) {
  const [projectName, setProjectName] = useState('')
  const [episodeName, setEpisodeName] = useState('')
  const [upgradeOk, setUpgradeOk] = useState(false)
  const legacy = project?.workflow_version === 'V1'
  const episode = episodes.find(e => e.id === episodeId)
  const next = progress?.next ?? null

  return <div className="stack v45 home-page">
    <div className="layout-two">
      <section className="panel"><div className="panel-heading"><div><h3>Dự án</h3><p>{project ? 'Dự án đang làm chọn ở thanh phía trên. Dữ liệu được giữ sau khi khởi động lại.' : 'Tạo dự án mới, hoặc chọn một dự án ở thanh phía trên.'}</p></div>{project && <span className={`status ${legacy ? 'warning' : 'success'}`}>{legacy ? 'Workflow cũ (V1)' : 'Workflow V4.5'}</span>}</div>
        {project && <p className="path-note"><strong>{project.name}</strong> · thư mục dữ liệu: <code>{project.root}</code></p>}
        <div className="form-row"><label className="field grow">Tạo dự án mới — tên dự án<input value={projectName} onChange={e => setProjectName(e.target.value)} placeholder="Ví dụ: Series Biển Đêm" /></label><button className={project ? '' : 'button-primary'} disabled={!projectName.trim()} onClick={() => onCreateProject(projectName.trim()).then(() => setProjectName(''))}>Tạo dự án</button></div>
        <p className="help">Dự án mới dùng workflow V4.5.</p>
      </section>
      <section className="panel"><div className="panel-heading"><div><h3>Các tập</h3><p>{project ? 'Tập đang làm dùng chung cho các tab Tập phim, Dựng shot và Final & xuất phim.' : 'Chọn dự án để quản lý các tập.'}</p></div>{project && <span className="count">{episodes.length} tập</span>}</div>
        {project ? <>
          {episodes.length ? <ul className="episode-list">{episodes.map(e => <li key={e.id} className={e.id === episodeId ? 'current' : ''}><span className="index">{String(e.ordinal).padStart(2, '0')}</span><span className="episode-name">{e.name}</span>{e.id === episodeId ? <span className="status success">Đang làm</span> : <button type="button" className="button-quiet" onClick={() => onSelectEpisode(e.id)}>Làm tập này</button>}</li>)}</ul>
            : <p className="help">Chưa có tập nào. Thêm tập đầu tiên để bắt đầu.</p>}
          <div className="form-row"><label className="field grow">Thêm tập — tên tập<input value={episodeName} onChange={e => setEpisodeName(e.target.value)} placeholder={`Ví dụ: Tập ${episodes.length + 1}`} /></label><button className={episodes.length ? '' : 'button-primary'} disabled={!episodeName.trim()} onClick={() => onAddEpisode(episodeName.trim()).then(() => setEpisodeName(''))}>Thêm tập</button></div>
        </> : <div className="empty"><span className="empty-icon" aria-hidden="true">◇</span><strong>Chưa chọn dự án</strong><p>Tạo dự án ở bên trái hoặc chọn dự án ở thanh phía trên.</p></div>}
      </section>
    </div>

    {project && legacy && <section className="panel"><div className="panel-heading"><div><h3>Dự án này đang dùng workflow cũ (V1)</h3><p>Các tab Thư viện series, Tập phim, Dựng shot và Final & xuất phim làm theo workflow V4.5. Chuyển dự án sang V4.5 để dùng chúng cùng cổng ngân sách tháng.</p></div><span className="status warning">Cần chuyển để dùng luồng mới</span></div>
      <p className="help">Sau khi chuyển: approval, output và biên nhận V1 được giữ làm lịch sử và vẫn xem được ở tab Workflow cũ (V1); chúng không được tính là checkpoint V4.5, nên các bước V4.5 phải được duyệt lại từ đầu. Việc chuyển không hoàn tác được trong app.</p>
      <div className="actions"><label className="acceptance-confirm"><input type="checkbox" checked={upgradeOk} onChange={e => setUpgradeOk(e.target.checked)} />Tôi hiểu các bước V4.5 phải được duyệt lại.</label><button className="button-primary" disabled={!upgradeOk} onClick={() => onUpgrade().then(() => setUpgradeOk(false))}>Chuyển dự án sang V4.5</button><button type="button" className="button-quiet" onClick={() => onGo('legacy')}>Mở các màn hình V1</button></div>
    </section>}

    {project && !legacy && progress && <section className="panel"><div className="panel-heading"><div><h3>Bản đồ workflow{episode ? ` · ${episode.name}` : ''}</h3><p>Bốn khu của sơ đồ V4.5 và các checkpoint bạn duyệt. Bấm vào một checkpoint để mở đúng bước.</p></div><span className={`status ${progress.total > 0 && progress.done === progress.total && progress.episode_id ? 'success' : 'neutral'}`}>{progress.done}/{progress.total} checkpoint đã duyệt</span></div>
      {next ? <div className="preflight ready settings-result flow-next-line"><span>Bước tiếp theo: <strong>{next.key} · {next.label}</strong>{next.detail ? ` — ${next.detail}` : ''}</span><button className="button-primary" onClick={() => onGo(next.page, next.step)}>Làm bước này</button></div>
        : progress.episode_id ? <div className="preflight ready settings-result">Tập này đã qua mọi checkpoint, từ CS1 đến C10.</div> : null}
      {!progress.episode_id && <p className="help">{episodes.length ? 'Chọn tập đang làm để thấy các checkpoint C1–C10 của tập.' : 'Thêm một tập để thấy các checkpoint C1–C10.'}</p>}
      <div className="zone-grid">{progress.zones.map(zone => <div key={zone.key} className="zone-card">
        <div className="zone-head"><span className="zone-tag">Khu {zone.key}</span><strong>{zone.label}</strong><button type="button" className="button-quiet" onClick={() => onGo(zone.page)}>Mở</button></div>
        {zone.checkpoints.length ? <ul>{zone.checkpoints.map(c => <li key={c.key}><button type="button" className={`zone-row ${c.state.toLowerCase()} ${next?.key === c.key ? 'next' : ''}`} onClick={() => onGo(c.page, c.step)}><span className="zone-sign" aria-hidden="true">{flowSign[c.state]}</span><span className="zone-key">{c.key}</span><span className="zone-label">{c.label}<small>{flowText[c.state]}{c.detail ? ` · ${c.detail}` : ''}</small></span></button></li>)}</ul>
          : <p className="help">Các checkpoint của khu này tính theo từng tập.</p>}
      </div>)}</div>
    </section>}

    {project && !legacy && hasLegacyData && <p className="help">Dự án này còn dữ liệu của workflow V1; xem ở tab Workflow cũ (V1).</p>}
  </div>
}
