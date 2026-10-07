import { useEffect, useState } from 'react'
import { api } from './api'
import { errorLabel } from './uiText'
import { t } from './i18n'

type Model = { model: string; label: string; platform: string; model_id: string; revision: number; key_configured: boolean; configured: boolean; verified_at: string | null; platforms: { id: string; label: string }[] }
type Settings = { models: Model[]; note: string }

export default function VideoModelSettingsPanel() {
  const [saved, setSaved] = useState<Settings | null>(null)
  const [drafts, setDrafts] = useState<Record<string, { platform: string; model_id: string }>>({})
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const apply = (value: Settings) => { setSaved(value); setDrafts(Object.fromEntries(value.models.map(m => [m.model, { platform: m.platform, model_id: m.model_id }]))) }
  useEffect(() => { api<Settings>('/settings/video-models').then(apply).catch(e => setError(errorLabel(e))) }, [])
  async function work(action: () => Promise<Settings>, result: string) {
    setBusy(true); setError(''); setMessage('')
    try { apply(await action()); setMessage(result) } catch (e) { setError(errorLabel(e)) } finally { setBusy(false) }
  }
  return <section className="panel"><div className="panel-heading"><div><h3>{t('Model video AI · Seedance 2.5 và MiniMax H3')}</h3><p>{t('Dùng cho shot AI và Hybrid. Nhập model ID (hoặc endpoint ID) đúng như trong tài khoản nhà cung cấp; khóa nhập trong cửa sổ bảo mật Windows.')}</p></div></div>
    {saved?.models.map(m => { const d = drafts[m.model] ?? { platform: m.platform, model_id: m.model_id }; return <div key={m.model} className="route-shot">
      <div className="route-shot-head"><strong>{m.label}</strong><span className={`status ${m.verified_at ? 'success' : 'neutral'}`}>{m.verified_at ? t('Đã nhận draft từ model này') : m.configured ? t('Đã lưu · chưa có draft nào') : t('Chưa cấu hình')}</span></div>
      <div className="planner-fields">
        <label className="field">{t('Nền tảng')}<select value={d.platform} onChange={e => setDrafts({ ...drafts, [m.model]: { ...d, platform: e.target.value } })}><option value="UNCONFIGURED">{t('Chưa chọn')}</option>{m.platforms.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}</select></label>
        <label className="field">{t('Model ID hoặc endpoint ID')}<input value={d.model_id} disabled={d.platform === 'UNCONFIGURED'} onChange={e => setDrafts({ ...drafts, [m.model]: { ...d, model_id: e.target.value } })} spellCheck={false} placeholder={t('Sao chép từ trang quản lý của nhà cung cấp')} /></label>
        <div className="field"><span>{t('Khóa')}</span><strong>{m.key_configured ? t('Đã có trong kho Windows') : t('Chưa có')}</strong></div>
      </div>
      <div className="actions">
        <button className="button-primary" disabled={busy || (d.platform === m.platform && d.model_id === m.model_id)} onClick={() => work(() => api(`/settings/video-models/${m.model}`, { expected_revision: m.revision, platform: d.platform, model_id: d.platform === 'UNCONFIGURED' ? '' : d.model_id.trim() }), t('Đã lưu cấu hình {0}. Chưa gửi yêu cầu nào.', m.label))}>{t('Lưu ')}{m.label}</button>
        <button disabled={busy || m.platform === 'UNCONFIGURED'} onClick={() => { setMessage(t('Đang mở cửa sổ Windows; kiểm tra thanh tác vụ nếu chưa thấy.')); work(() => api(`/settings/video-models/${m.model}/key/prompt`, {}), t('Đã lưu khóa {0} vào kho Windows. Giá trị khóa không đi qua trang web.', m.label)) }}>{t('Nhập khóa trong cửa sổ Windows')}</button>
        <button disabled={busy || !m.key_configured} onClick={() => work(() => api(`/settings/video-models/${m.model}/key/delete`, {}), t('Đã xóa khóa {0}.', m.label))}>{t('Xóa khóa')}</button>
      </div>
    </div> })}
    {error && <div className="alert error settings-result" role="alert">{error}</div>}
    {message && <div className="preflight ready settings-result" role="status">{message}</div>}
    <p className="help">{saved?.note}{t(' Giá mỗi giây nhập ở C3 của tab Tập phim. Mỗi yêu cầu draft đi qua trần chi phí tháng (cổng ngân sách ②) và tắt audio tự sinh.')}</p>
  </section>
}
