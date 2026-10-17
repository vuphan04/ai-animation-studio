import { useEffect, useState } from 'react'
import { api, type AISettings } from './api'
import { errorLabel } from './uiText'
import { t } from './i18n'

type KeyProvider = 'OPENAI' | 'CLAUDE_API'

export default function AISettingsPanel() {
  const [current, setCurrent] = useState<AISettings | null>(null)
  const [provider, setProvider] = useState<AISettings['provider_kind']>('LOCAL_PLANNER_FIXTURE')
  const [model, setModel] = useState('gpt-6.1-sol')
  const [tokens, setTokens] = useState(8192)
  const [claudeModel, setClaudeModel] = useState('claude-opus-5-5')
  const [effort, setEffort] = useState('medium')
  const [claudeTokens, setClaudeTokens] = useState(16000)
  const [fallback, setFallback] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  async function load() {
    const value = await api<AISettings>('/settings/ai')
    setCurrent(value); setProvider(value.provider_kind); setModel(value.model); setTokens(value.max_output_tokens)
    setClaudeModel(value.claude_model); setEffort(value.claude_effort); setClaudeTokens(value.claude_max_output_tokens); setFallback(value.claude_fallback)
  }
  useEffect(() => { load().catch(e => setError(errorLabel(e))) }, [])
  async function run(action: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('')
    try { await action() } catch (e) { setNotice(''); setError(errorLabel(e)) } finally { setBusy(false) }
  }
  const selectedModel = current?.available_models.find(item => item.id === model)
  const selectedClaude = current?.claude_models.find(item => item.id === claudeModel)
  const openai = provider === 'OPENAI'
  const claude = provider === 'CLAUDE_API'
  const keyProvider: KeyProvider | null = openai ? 'OPENAI' : claude ? 'CLAUDE_API' : null
  const keySaved = claude ? current?.claude_key_configured : current?.key_configured
  const tokensOk = tokens >= 512 && tokens <= 16384 && claudeTokens >= 1024 && claudeTokens <= 64000
  // The chip describes the saved mode, not the one being picked in the list.
  const chip = !current ? { tone: 'neutral', text: t('Đang tải…') }
    : current.provider_kind === 'OPENAI' ? { tone: current.key_configured ? 'success' : 'warning', text: current.key_configured ? t('OPENAI · ĐÃ LƯU KHÓA') : t('OPENAI · THIẾU KHÓA') }
      : current.provider_kind === 'CLAUDE_API' ? { tone: current.claude_key_configured ? 'success' : 'warning', text: current.claude_key_configured ? t('CLAUDE API · ĐÃ LƯU KHÓA') : t('CLAUDE API · THIẾU KHÓA') }
        : current.provider_kind === 'CLAUDE_CODE' ? { tone: current.configured ? 'success' : 'warning', text: current.configured ? 'CLAUDE CODE' : t('CHƯA TÌM THẤY CLAUDE CODE') }
          : { tone: 'warning', text: t('MÔ PHỎNG') }

  function save() {
    return run(async () => {
      const saved = await api<AISettings>('/settings/ai', { provider_kind: provider, model, max_output_tokens: tokens, claude_model: claudeModel, claude_effort: effort,
        claude_max_output_tokens: claudeTokens, claude_fallback: fallback, expected_revision: current!.revision })
      setCurrent(saved); setNotice(t('Đã lưu cấu hình AI. Chưa gửi yêu cầu tạo nội dung.'))
    })
  }
  function promptKey(kind: KeyProvider) {
    return run(async () => {
      setNotice(t('Đang mở cửa sổ bảo mật Windows. Nếu chưa thấy, hãy kiểm tra thanh tác vụ.'))
      setCurrent(await api<AISettings>('/settings/ai/key/prompt', { provider_kind: kind }))
      setNotice(t('Đã lưu khóa trong Windows Credential Manager. Giá trị khóa không đi qua trang web.'))
    })
  }
  function deleteKey(kind: KeyProvider) {
    return run(async () => {
      setCurrent(await api<AISettings>('/settings/ai/key/delete', { provider_kind: kind }))
      setNotice(kind === 'CLAUDE_API' ? t('Đã xóa khóa Claude API khỏi kho bảo mật Windows.') : t('Đã xóa khóa OpenAI khỏi kho bảo mật Windows.'))
    })
  }
  function check(kind: KeyProvider) {
    return run(async () => {
      // The server checks the saved model, which the answer names.
      const result = await api<{ message: string }>('/settings/ai/check', { provider_kind: kind })
      setNotice(result.message)
    })
  }

  return <section className="panel ai-settings-panel">
    <div className="panel-heading"><div><h3>{t('Phân tích kịch bản bằng AI')}</h3><p>{t('Chọn công cụ liệt kê nhân vật, bối cảnh (S2) và chia scene/shot (03): mô phỏng, OpenAI API, Claude API, hoặc Claude qua Claude Code trên máy này.')}</p></div><span className={`status ${chip.tone}`}>{chip.text}</span></div>
    {error && <div className="alert error" role="alert">{error}</div>}
    {notice && <div className="alert notice" role="status">{notice}</div>}
    <div className="planner-fields">
      <label className="field">{t('Chế độ phân tích kịch bản')}<select value={provider} onChange={e => setProvider(e.target.value as AISettings['provider_kind'])}><option value="LOCAL_PLANNER_FIXTURE">{t('Mô phỏng để thử luồng, không gọi AI')}</option><option value="OPENAI">{t('OpenAI API · có phí')}</option><option value="CLAUDE_API">{t('Claude API · có phí')}</option><option value="CLAUDE_CODE">{t('Claude qua Claude Code trên máy này')}</option></select></label>
      {openai && <label className="field">{t('Mô hình OpenAI')}<select value={model} onChange={e => { setModel(e.target.value); if (e.target.value.startsWith('gpt-6')) setTokens(Math.max(tokens, 8192)) }}>{current?.available_models.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>}
      {openai && <label className="field">{t('Giới hạn token đầu ra')}<input type="number" min={512} max={16384} value={tokens} onChange={e => setTokens(Number(e.target.value))} /></label>}
      {claude && <label className="field">{t('Mô hình Claude')}<select value={claudeModel} onChange={e => setClaudeModel(e.target.value)}>{current?.claude_models.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>}
      {claude && <label className="field">{t('Mức suy nghĩ')}<select value={effort} onChange={e => setEffort(e.target.value)}>{current?.claude_efforts.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>}
      {claude && <label className="field">{t('Giới hạn token đầu ra')}<input type="number" min={1024} max={64000} step={1000} value={claudeTokens} onChange={e => setClaudeTokens(Number(e.target.value))} /></label>}
    </div>
    {claude && selectedClaude?.fallback && <label className="acceptance-confirm"><input type="checkbox" checked={fallback} onChange={e => setFallback(e.target.checked)} />{t('Khi Claude từ chối theo chính sách an toàn, cho Anthropic chạy lại trên model dự phòng (khoản giữ ngân sách tính thêm lần chạy này)')}</label>}
    <div className="actions"><button className="button-primary" disabled={busy || !current || !tokensOk} onClick={save}>{t('Lưu cấu hình AI')}</button>
      {keyProvider && <><button disabled={busy} onClick={() => promptKey(keyProvider)}>{t('Nhập khóa trong cửa sổ Windows')}</button><button disabled={busy || !keySaved} onClick={() => deleteKey(keyProvider)}>{t('Xóa khóa')}</button><button disabled={busy || !keySaved} onClick={() => check(keyProvider)}>{t('Kiểm tra kết nối')}</button></>}
      <button className="button-quiet" disabled={busy} onClick={() => run(load)}>{t('Tải lại')}</button></div>
    {openai && <p className="help">{selectedModel && t('Giá tham chiếu {0}: ${1} / 1 triệu token vào, ${2} / 1 triệu token ra. ', selectedModel.label, selectedModel.input_rate, selectedModel.output_rate)}{t('Bảng giá có thể đổi; dự trù trong app tính thêm biên cho cache write. Trạng thái khóa và nút kiểm tra kết nối chưa chứng minh một lần tạo nháp thành công. Mỗi yêu cầu có phí đi qua trần chi phí tháng đặt ở S1.')}{selectedModel?.legacy && t(' Model này là lựa chọn cũ; hãy cân nhắc dòng GPT-6.')}</p>}
    {claude && <p className="help">{selectedClaude && t('Giá tham chiếu {0}: ${1} / 1 triệu token vào, ${2} / 1 triệu token ra. ', selectedClaude.label, selectedClaude.input_rate, selectedClaude.output_rate)}{selectedClaude?.long_prompt && t('Khi prompt trên {0}K token: ${1} vào, ${2} ra. ', selectedClaude.long_prompt.above_tokens / 1000, selectedClaude.long_prompt.input_rate, selectedClaude.long_prompt.output_rate)}{t('Dùng API key của tài khoản Anthropic (console.anthropic.com), tính phí riêng với gói Claude Pro/Max. Khóa được nhập trong cửa sổ Windows và lưu ở Windows Credential Manager. Token suy nghĩ tính vào giới hạn token đầu ra; mức suy nghĩ cao cho kết quả kỹ hơn nhưng tốn hơn. Mỗi yêu cầu có phí đi qua trần chi phí tháng đặt ở S1; app không tự gửi lại khi kết quả chưa rõ. Kiểm tra kết nối không tạo nội dung và không tính phí.')}</p>}
    {provider === 'CLAUDE_CODE' && <p className="help">{t('Chế độ này dùng Claude Code đã cài và đã đăng nhập trên máy. Xem trạng thái, chọn model và gửi yêu cầu thử ở khung “Claude qua Claude Code trên máy này” bên dưới.')}</p>}
    {provider === 'LOCAL_PLANNER_FIXTURE' && <p className="help">{t('Chế độ mô phỏng tách tên và chia shot theo quy tắc cố định để thử luồng; không gọi model AI, không dịch và không viết prompt.')}</p>}
  </section>
}
