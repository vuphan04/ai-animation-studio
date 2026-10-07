import { useState } from 'react'
import type { FlowState } from './api'
import StepTabs, { type StepDef } from './StepTabs'
import UeBranchPanel from './UeBranchPanel'
import ProxyPanel from './ProxyPanel'
import AiBranchPanel from './AiBranchPanel'
import RoughCutPanel from './RoughCutPanel'
import { t } from './i18n'

export const productionSteps: StepDef[] = [
  { id: 'ue', code: '07–08 · C5', label: t('Nhánh UE') },
  { id: 'proxy', code: '09 · C6', label: 'Cache & proxy' },
  { id: 'ai', code: '07–09', label: t('Nhánh AI & Hybrid') },
  { id: 'rough', code: 'C7', label: t('Rough cut & khóa dựng') },
]

export default function ProductionPage({ projectId, episodeId, step, onStep, marks }: { projectId: string; episodeId: string; step: string; onStep: (step: string) => void; marks: Record<string, FlowState> }) {
  // Each panel reloads when something upstream of it changed, never because of its own actions.
  const [ueVersion, setUeVersion] = useState(0)
  const [proxyVersion, setProxyVersion] = useState(0)
  const [aiVersion, setAiVersion] = useState(0)
  if (!projectId) return <div className="empty"><span className="empty-icon" aria-hidden="true">◇</span><strong>{t('Chưa chọn dự án')}</strong><p>{t('Chọn dự án ở thanh phía trên để dựng shot của từng tập.')}</p></div>
  if (!episodeId) return <div className="empty"><span className="empty-icon" aria-hidden="true">◇</span><strong>{t('Chưa chọn tập')}</strong><p>{t('Chọn tập đang làm ở thanh phía trên, hoặc thêm tập ở tab Tổng quan.')}</p></div>
  return <div className="stack v45 production-page">
    <StepTabs steps={productionSteps} value={step} onChange={onStep} marks={marks} label={t('Các bước của Khu B')} />
    <p className="help step-intro">{t('Mỗi shot đi theo nhánh đã khóa ở C3: UE dựng trong Unreal, AI tạo video trực tiếp, Hybrid dùng nguồn UE rồi tạo draft AI. Mọi nhánh gặp nhau ở rough cut và khóa dựng (C7).')}</p>
    <div className="stack" hidden={step !== 'ue'}><UeBranchPanel key={`ue-${episodeId}`} projectId={projectId} episodeId={episodeId} onChanged={() => setUeVersion(v => v + 1)} /></div>
    <div className="stack" hidden={step !== 'proxy'}><ProxyPanel key={`proxy-${episodeId}`} episodeId={episodeId} refreshKey={ueVersion} onChanged={() => setProxyVersion(v => v + 1)} /></div>
    <div className="stack" hidden={step !== 'ai'}><AiBranchPanel key={`ai-${episodeId}`} episodeId={episodeId} refreshKey={ueVersion + proxyVersion} onChanged={() => setAiVersion(v => v + 1)} /></div>
    <div className="stack" hidden={step !== 'rough'}><RoughCutPanel key={`rough-${episodeId}`} episodeId={episodeId} refreshKey={ueVersion + proxyVersion + aiVersion} /></div>
  </div>
}
