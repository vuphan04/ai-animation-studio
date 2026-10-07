import type { Checkpoint, FlowState, Progress } from './api'
import { t } from './i18n'

export const flowSign: Record<FlowState, string> = { DONE: '✓', PARTIAL: '◐', STALE: '!', TODO: '○' }
export const flowText: Record<FlowState, string> = { DONE: t('Đã duyệt'), PARTIAL: t('Đang làm dở'), STALE: t('Đã đổi sau khi duyệt'), TODO: t('Chưa duyệt') }

/** The whole workflow on one line: every checkpoint of the working episode, and the step to do next. */
export default function WorkflowBar({ progress, page, gpu, onGo }: { progress: Progress | null; page: string; gpu: string | null; onGo: (page: string, step?: string) => void }) {
  if (!progress) return null
  const next: Checkpoint | null = progress.next
  return <div className="flowbar" aria-label={t('Tiến độ workflow của tập đang làm')}>
    <div className="flow-zones">{progress.zones.map(zone => <div key={zone.key} className={`flow-zone ${zone.page === page ? 'active' : ''}`}>
      <button type="button" className="flow-zone-name" title={zone.label} onClick={() => onGo(zone.page)}>{t('Khu {0}', zone.key)}</button>
      {zone.checkpoints.length ? zone.checkpoints.map(c => <button key={c.key} type="button" className={`flow-chip ${c.state.toLowerCase()} ${next?.key === c.key ? 'next' : ''}`} title={`${c.label} · ${flowText[c.state]}${c.detail ? ` · ${c.detail}` : ''}`} onClick={() => onGo(c.page, c.step)}><span aria-hidden="true">{flowSign[c.state]}</span> {c.key}</button>) : <span className="flow-empty">{t('chọn tập')}</span>}
    </div>)}</div>
    <div className="flow-next">
      {next ? <><span>{t('Bước tiếp theo')}</span><strong>{next.key} · {next.label}</strong><small>{next.detail}</small><button type="button" className="button-primary" onClick={() => onGo(next.page, next.step)}>{t('Đi tới')}</button></>
        : progress.episode_id ? <strong>{t('Tập này đã qua mọi checkpoint.')}</strong> : <span>{t('Chọn tập đang làm để xem các bước của tập.')}</span>}
      {gpu && <small className="flow-gpu">{gpu}</small>}
    </div>
  </div>
}
