import { useEffect, useState } from 'react'
import { api } from './api'
import { errorLabel } from './uiText'
import { t } from './i18n'

type Status = { installed: boolean; version: string | null; logged_in: boolean; auth_method: string; subscription_type: string | null; error: string | null }
type Settings = { revision: number; executable_path: string; detected_path: string; effective_path: string; model: string; verified_at: string | null; verified_model: string | null; available_models: { id: string; label: string }[]; status: Status }

/** Claude through the Claude Code program on this machine. The app shows its state; signing in happens inside Claude Code. */
export default function ClaudeCodePanel() {
  const [saved, setSaved] = useState<Settings | null>(null)
  const [path, setPath] = useState('')
  const [model, setModel] = useState('opus')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  function apply(value: Settings) { setSaved(value); setPath(value.executable_path); setModel(value.model) }
  useEffect(() => { api<Settings>('/settings/claude-code').then(apply).catch(e => setError(errorLabel(e))) }, [])
  async function run(start: string, action: () => Promise<void>) {
    setBusy(true); setError(''); setNotice(start)
    try { await action() } catch (e) { setNotice(''); setError(errorLabel(e)) } finally { setBusy(false) }
  }
  const status = saved?.status
  const changed = Boolean(saved && (path.trim() !== saved.executable_path || model !== saved.model))
  const ready = Boolean(status?.installed && status.logged_in)
  const tone = !saved ? 'neutral' : !ready ? 'warning' : saved.verified_at && !changed ? 'success' : 'neutral'
  const label = !saved ? t('Đang kiểm tra…') : !status!.installed ? t('Chưa tìm thấy Claude Code') : !status!.logged_in ? t('Chưa đăng nhập') : changed ? t('Chưa lưu') : saved.verified_at ? t('Đã trả lời yêu cầu thử') : t('Đã đăng nhập · chưa gửi thử')
  // Claude Code may be signed in with a subscription or with pay-per-token credentials; the operator should know which.
  const account = status?.auth_method === 'claude.ai' ? t('gói đăng ký Claude{0}', status.subscription_type ? ` (${status.subscription_type})` : '') : t('khóa API hoặc tài khoản Console, tính phí theo token ở tài khoản đó')

  return <section className="panel">
    <div className="panel-heading"><div><h3>{t('Claude qua Claude Code trên máy này')}</h3><p>{t('App gọi chương trình Claude Code đã cài trên máy này để liệt kê nhân vật, bối cảnh (S2) và chia scene/shot (03). App không hỏi, không thấy và không lưu tài khoản Claude: bạn đăng nhập trong Claude Code bằng tài khoản của chính mình.')}</p></div><span className={`status ${tone}`}>{label}</span></div>
    {error && <div className="alert error" role="alert">{error}</div>}
    {notice && <div className="alert notice" role="status">{notice}</div>}
    {saved && <div className="info-line"><span>{status!.installed ? <>Claude Code <strong>{status!.version}</strong>{status!.logged_in ? t(' · đăng nhập bằng {0}', account) : t(' · chưa đăng nhập')}</> : t('Chưa tìm thấy chương trình Claude Code')}</span>{saved.effective_path && <code>{saved.effective_path}</code>}</div>}
    {status?.error && <div className="preflight blocked settings-result">{status.error}</div>}
    <div className="settings-fields">
      <div className="setting-file"><label className="field grow">{t('Tệp claude.exe của Claude Code')}<input value={path} disabled={busy} onChange={e => setPath(e.target.value)} placeholder={t('Để trống: app tự tìm trên máy này{0}', saved?.detected_path ? ` — ${saved.detected_path}` : '')} spellCheck={false} /></label><button type="button" disabled={busy} onClick={() => run(t('Đang mở hộp thoại chọn tệp Windows…'), async () => { const picked = await api<{ path: string | null }>('/settings/claude-code/browse', {}); if (picked.path) { setPath(picked.path); setNotice(t('Đã chọn tệp. Bấm Lưu cấu hình.')) } else setNotice(t('Đã hủy chọn tệp.')) })}>{t('Duyệt…')}</button></div>
      <div className="planner-fields"><label className="field">{t('Model Claude')}<select value={model} disabled={busy} onChange={e => setModel(e.target.value)}>{saved?.available_models.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label></div>
    </div>
    <div className="actions">
      <button className="button-primary" disabled={busy || !saved || !changed} onClick={() => run(t('Đang kiểm tra và lưu cấu hình…'), async () => { apply(await api<Settings>('/settings/claude-code', { expected_revision: saved!.revision, executable_path: path, model })); setNotice(t('Đã lưu cấu hình Claude Code; cấu hình được giữ sau khi khởi động lại.')) })}>{t('Lưu cấu hình')}</button>
      <button disabled={busy} onClick={() => run(t('Đang hỏi Claude Code về trạng thái…'), async () => { apply(await api<Settings>('/settings/claude-code')); setNotice(t('Đã kiểm tra lại. Bước này không gửi gì tới model.')) })}>{t('Kiểm tra lại')}</button>
      <button disabled={busy || !ready || changed} onClick={() => run(t('Đang gửi một yêu cầu thử qua Claude Code…'), async () => { apply(await api<Settings>('/settings/claude-code/test', {})); setNotice(t('Claude Code đã trả lời yêu cầu thử.')) })}>{t('Gửi một yêu cầu thử')}</button>
    </div>
    {saved?.verified_at && <p className="help">{t('Lần trả lời gần nhất: {0}', saved.verified_at.slice(0, 16).replace('T', ' '))}{saved.verified_model ? ` · ${saved.verified_model}` : ''}</p>}
    <p className="help">{t('“Đã đăng nhập” chỉ cho biết Claude Code có tài khoản; yêu cầu thử hoặc một lần phân tích thật mới cho thấy nó trả lời được. Mỗi yêu cầu chạy không kèm công cụ nào và không dùng cài đặt riêng của Claude Code. Lượt dùng tính vào hạn mức của tài khoản đang đăng nhập và không ghi vào Sổ chi phí của app. Để dùng, chọn “Claude qua Claude Code trên máy này” ở khung phía trên rồi lưu.')}</p>
  </section>
}
