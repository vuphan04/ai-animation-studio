import { useState } from 'react'
import type { FlowState } from './api'
import StepTabs, { type StepDef } from './StepTabs'
import FinalPanel from './FinalPanel'
import SoundPanel from './SoundPanel'
import DeliveryPanel from './DeliveryPanel'
import { t } from './i18n'

export const finalsSteps: StepDef[] = [
  { id: 'final', code: '10–11 · C8', label: t('Final & chuẩn hóa') },
  { id: 'sound', code: '12 · C9', label: t('Foley & âm thanh') },
  { id: 'delivery', code: '13 · C10', label: t('Gói bàn giao & xuất phim') },
]

export default function FinalsPage({ projectId, episodeId, step, onStep, marks }: { projectId: string; episodeId: string; step: string; onStep: (step: string) => void; marks: Record<string, FlowState> }) {
  // The hand-off panel reloads when finals or sound change; neither reloads because of its own actions.
  const [finalVersion, setFinalVersion] = useState(0)
  const [soundVersion, setSoundVersion] = useState(0)
  if (!projectId) return <div className="empty"><span className="empty-icon" aria-hidden="true">◇</span><strong>{t('Chưa chọn dự án')}</strong><p>{t('Chọn dự án ở thanh phía trên để tạo final và xuất phim của từng tập.')}</p></div>
  if (!episodeId) return <div className="empty"><span className="empty-icon" aria-hidden="true">◇</span><strong>{t('Chưa chọn tập')}</strong><p>{t('Chọn tập đang làm ở thanh phía trên, hoặc thêm tập ở tab Tổng quan.')}</p></div>
  return <div className="stack v45 production-page">
    <StepTabs steps={finalsSteps} value={step} onChange={onStep} marks={marks} label={t('Các bước của Khu C')} />
    <p className="help step-intro">{t('Sau khi khóa dựng (C7): final và chuẩn hóa từng shot, âm thanh trên hình đã khóa, rồi gói timeline kèm QC để import vào DaVinci Resolve và xuất phim.')}</p>
    <div className="stack" hidden={step !== 'final'}><FinalPanel key={`final-${episodeId}`} episodeId={episodeId} onChanged={() => setFinalVersion(v => v + 1)} /></div>
    <div className="stack" hidden={step !== 'sound'}><SoundPanel key={`sound-${episodeId}`} episodeId={episodeId} refreshKey={0} onChanged={() => setSoundVersion(v => v + 1)} /></div>
    <div className="stack" hidden={step !== 'delivery'}><DeliveryPanel key={`delivery-${episodeId}`} episodeId={episodeId} refreshKey={finalVersion + soundVersion} /></div>
  </div>
}
