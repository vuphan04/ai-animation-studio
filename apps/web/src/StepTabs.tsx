import type { FlowState } from './api'
import { t } from './i18n'

export type StepDef = { id: string; code?: string; label: string }

const markText: Record<FlowState, string> = { DONE: t('đã duyệt'), PARTIAL: t('đang làm dở'), STALE: t('đã đổi sau khi duyệt'), TODO: t('chưa duyệt') }
const markSign: Record<FlowState, string> = { DONE: '✓', PARTIAL: '◐', STALE: '!', TODO: '' }

/** The steps of one screen, one shown at a time. `marks` carries the state of the checkpoint approved in a step. */
export default function StepTabs({ steps, value, onChange, marks, label }: { steps: StepDef[]; value: string; onChange: (id: string) => void; marks?: Record<string, FlowState>; label: string }) {
  return <div className="step-tabs" role="tablist" aria-label={label}>
    {steps.map(step => { const mark = marks?.[step.id]; return <button key={step.id} type="button" role="tab" aria-selected={value === step.id} className={`${value === step.id ? 'active' : ''} ${mark ? mark.toLowerCase() : ''}`} onClick={() => onChange(step.id)}>
      {step.code && <span className="step-code">{step.code}</span>}<span>{step.label}</span>{mark && markSign[mark] && <span className="step-mark" title={markText[mark]} aria-label={markText[mark]}>{markSign[mark]}</span>}
    </button> })}
  </div>
}
