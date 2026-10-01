import { useEffect, useState } from 'react'
import { api, type AISettings } from './api'
import { errorLabel } from './uiText'

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

  return <section className="panel ai-settings-panel">
    <div className="panel-heading"><div><h3>Dịch vụ AI · OpenAI API</h3><p>Tạo bản nháp kế hoạch bằng dịch vụ đám mây. Ứng dụng không chạy mô hình AI trên máy này.</p></div><span className={`status ${current?.provider_kind === 'OPENAI' && current.key_configured ? 'success' : 'warning'}`}>{current?.provider_kind === 'OPENAI' ? current.key_configured ? 'ĐÃ LƯU KHÓA' : 'THIẾU KHÓA' : 'MÔ PHỎNG'}</span></div>
    {error && <div className="alert error" role="alert">{error}</div>}
    {notice && <div className="alert notice" role="status">{notice}</div>}
    <div className="planner-fields">
      <label className="field">Chế độ lập kế hoạch<select value={provider} onChange={e => setProvider(e.target.value as AISettings['provider_kind'])}><option value="LOCAL_PLANNER_FIXTURE">Mô phỏng để thử luồng, không gọi AI</option><option value="OPENAI">OpenAI API · có phí</option></select></label>
      <label className="field">Mô hình OpenAI<select value={model} onChange={e => { setModel(e.target.value); if (e.target.value.startsWith('gpt-6')) setTokens(Math.max(tokens, 8192)) }}>{current?.available_models.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
      <label className="field">Giới hạn token đầu ra<input type="number" min={512} max={16384} value={tokens} onChange={e => setTokens(Number(e.target.value))} /></label>
    </div>
    <div className="actions"><button className="button-primary" disabled={busy || !current || tokens < 512 || tokens > 16384} onClick={() => run(async () => { const saved = await api<AISettings>('/settings/ai', { provider_kind: provider, model, max_output_tokens: tokens, expected_revision: current!.revision }); setCurrent(saved); setNotice('Đã lưu cấu hình AI. Chưa gửi yêu cầu tạo nội dung.') })}>Lưu cấu hình AI</button><button disabled={busy} onClick={() => run(async () => { setNotice('Đang mở cửa sổ bảo mật Windows. Nếu chưa thấy, hãy kiểm tra thanh tác vụ.'); setCurrent(await api<AISettings>('/settings/ai/key/prompt', {})); setNotice('Đã lưu khóa trong Windows Credential Manager. Giá trị khóa không đi qua trang web.') })}>Nhập khóa trong cửa sổ Windows</button><button disabled={busy || !current?.key_configured} onClick={() => run(async () => { setCurrent(await api<AISettings>('/settings/ai/key/delete', {})); setNotice('Đã xóa khóa OpenAI khỏi kho bảo mật Windows.') })}>Xóa khóa</button><button disabled={busy || !current?.key_configured} onClick={() => run(async () => { const result = await api<{ message: string }>('/settings/ai/check', {}); setNotice(result.message) })}>Kiểm tra kết nối</button><button className="button-quiet" disabled={busy} onClick={() => run(load)}>Tải lại</button></div>
    <p className="help">{selectedModel && `Giá tham chiếu ${selectedModel.label}: $${selectedModel.input_rate} / 1 triệu token vào, $${selectedModel.output_rate} / 1 triệu token ra. `}Bảng giá có thể đổi; dự trù trong app tính thêm biên cho cache write. Trạng thái khóa và nút kiểm tra kết nối chưa chứng minh một lần tạo nháp thành công. Tạo nháp cần cấp ngân sách riêng trong Lập kế hoạch.{selectedModel?.legacy && ' Model này là lựa chọn cũ; hãy cân nhắc dòng GPT-6.'}</p>
  </section>
}
