import { useEffect, useState } from 'react'
import { api, type AISettings } from './api'
import { errorLabel } from './uiText'
import { t } from './i18n'

export default function AISettingsPanel() {
  const [current, setCurrent] = useState<AISettings | null>(null)
  const [provider, setProvider] = useState<AISettings['provider_kind']>('LOCAL_PLANNER_FIXTURE')
  const [model, setModel] = useState('gpt-6.1-sol')
  const [tokens, setTokens] = useState(8192)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  async function load() {
    const value = await api<AISettings>('/settings/ai')
    setCurrent(value); setProvider(value.provider_kind); setModel(value.model); setTokens(value.max_output_tokens)
  }
  useEffect(() => { load().catch(e => setError(errorLabel(e))) }, [])
  async function run(action: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('')
    try { await action() } catch (e) { setNotice(''); setError(errorLabel(e)) } finally { setBusy(false) }
  }
  const selectedModel = current?.available_models.find(item => item.id === model)
  const openai = provider === 'OPENAI'
  // The chip describes the saved mode, not the one being picked in the list.
  const chip = !current ? { tone: 'neutral', text: t('Đang tải…') }
    : current.provider_kind === 'OPENAI' ? { tone: current.key_configured ? 'success' : 'warning', text: current.key_configured ? t('OPENAI · ĐÃ LƯU KHÓA') : t('OPENAI · THIẾU KHÓA') }
      : current.provider_kind === 'CLAUDE_CODE' ? { tone: current.configured ? 'success' : 'warning', text: current.configured ? 'CLAUDE CODE' : t('CHƯA TÌM THẤY CLAUDE CODE') }
        : { tone: 'warning', text: t('MÔ PHỎNG') }

  return <section className="panel ai-settings-panel">
    <div className="panel-heading"><div><h3>{t('Phân tích kịch bản bằng AI')}</h3><p>{t('Chọn công cụ liệt kê nhân vật, bối cảnh (S2) và chia scene/shot (03): mô phỏng, OpenAI API, hoặc Claude qua Claude Code trên máy này.')}</p></div><span className={`status ${chip.tone}`}>{chip.text}</span></div>
    {error && <div className="alert error" role="alert">{error}</div>}
    {notice && <div className="alert notice" role="status">{notice}</div>}
    <div className="planner-fields">
      <label className="field">{t('Chế độ phân tích kịch bản')}<select value={provider} onChange={e => setProvider(e.target.value as AISettings['provider_kind'])}><option value="LOCAL_PLANNER_FIXTURE">{t('Mô phỏng để thử luồng, không gọi AI')}</option><option value="OPENAI">{t('OpenAI API · có phí')}</option><option value="CLAUDE_CODE">{t('Claude qua Claude Code trên máy này')}</option></select></label>
      {openai && <label className="field">{t('Mô hình OpenAI')}<select value={model} onChange={e => { setModel(e.target.value); if (e.target.value.startsWith('gpt-6')) setTokens(Math.max(tokens, 8192)) }}>{current?.available_models.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>}
      {openai && <label className="field">{t('Giới hạn token đầu ra')}<input type="number" min={512} max={16384} value={tokens} onChange={e => setTokens(Number(e.target.value))} /></label>}
    </div>
    <div className="actions"><button className="button-primary" disabled={busy || !current || tokens < 512 || tokens > 16384} onClick={() => run(async () => { const saved = await api<AISettings>('/settings/ai', { provider_kind: provider, model, max_output_tokens: tokens, expected_revision: current!.revision }); setCurrent(saved); setNotice(t('Đã lưu cấu hình AI. Chưa gửi yêu cầu tạo nội dung.')) })}>{t('Lưu cấu hình AI')}</button>
      {openai && <><button disabled={busy} onClick={() => run(async () => { setNotice(t('Đang mở cửa sổ bảo mật Windows. Nếu chưa thấy, hãy kiểm tra thanh tác vụ.')); setCurrent(await api<AISettings>('/settings/ai/key/prompt', {})); setNotice(t('Đã lưu khóa trong Windows Credential Manager. Giá trị khóa không đi qua trang web.')) })}>{t('Nhập khóa trong cửa sổ Windows')}</button><button disabled={busy || !current?.key_configured} onClick={() => run(async () => { setCurrent(await api<AISettings>('/settings/ai/key/delete', {})); setNotice(t('Đã xóa khóa OpenAI khỏi kho bảo mật Windows.')) })}>{t('Xóa khóa')}</button><button disabled={busy || !current?.key_configured} onClick={() => run(async () => { const result = await api<{ message: string }>('/settings/ai/check', {}); setNotice(result.message) })}>{t('Kiểm tra kết nối')}</button></>}
      <button className="button-quiet" disabled={busy} onClick={() => run(load)}>{t('Tải lại')}</button></div>
    {openai && <p className="help">{selectedModel && t('Giá tham chiếu {0}: ${1} / 1 triệu token vào, ${2} / 1 triệu token ra. ', selectedModel.label, selectedModel.input_rate, selectedModel.output_rate)}{t('Bảng giá có thể đổi; dự trù trong app tính thêm biên cho cache write. Trạng thái khóa và nút kiểm tra kết nối chưa chứng minh một lần tạo nháp thành công. Mỗi yêu cầu có phí đi qua trần chi phí tháng đặt ở S1.')}{selectedModel?.legacy && t(' Model này là lựa chọn cũ; hãy cân nhắc dòng GPT-6.')}</p>}
    {provider === 'CLAUDE_CODE' && <p className="help">{t('Chế độ này dùng Claude Code đã cài và đã đăng nhập trên máy. Xem trạng thái, chọn model và gửi yêu cầu thử ở khung “Claude qua Claude Code trên máy này” bên dưới.')}</p>}
    {provider === 'LOCAL_PLANNER_FIXTURE' && <p className="help">{t('Chế độ mô phỏng tách tên và chia shot theo quy tắc cố định để thử luồng; không gọi model AI, không dịch và không viết prompt.')}</p>}
  </section>
}
