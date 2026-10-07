import { useEffect, useState } from 'react'
import { api } from './api'
import { errorLabel } from './uiText'

type Settings = { revision: number; platform: string; model_id: string; key_configured: boolean; configured: boolean; operational: boolean; blockers: string[] }

export default function MediaSettingsPanel() {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [platform, setPlatform] = useState('UNCONFIGURED')
  const [modelId, setModelId] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  function apply(value: Settings) { setSettings(value); setPlatform(value.platform); setModelId(value.model_id) }
  useEffect(() => { api<Settings>('/settings/media').then(apply).catch(e => setError(errorLabel(e))) }, [])
  async function work(action: () => Promise<Settings>, result: string) {
    setBusy(true); setError(''); setMessage('')
    try { apply(await action()); setMessage(result) } catch (e) { setError(errorLabel(e)) } finally { setBusy(false) }
  }
  return <section className="panel"><div className="panel-heading"><div><h3>Dịch vụ video · Seedance</h3><p>Chọn nền tảng và ID mô hình từ tài khoản của bạn. Khóa được nhập trong cửa sổ bảo mật Windows.</p></div><span className="status warning">CHƯA KIỂM CHỨNG LIVE</span></div>
    <div className="planner-fields"><label className="field">Nền tảng<select value={platform} onChange={e => setPlatform(e.target.value)}><option value="UNCONFIGURED">Chưa chọn</option><option value="BYTEPLUS">BytePlus ModelArk · USD</option><option value="VOLCENGINE">Volcengine Ark · CNY</option></select></label><label className="field">Model / endpoint ID<input value={modelId} onChange={e => setModelId(e.target.value)} placeholder="Sao chép ID từ tài khoản provider" autoComplete="off" /></label><div className="field"><span>Trạng thái khóa đã lưu</span><strong>{settings?.key_configured ? 'Đã có khóa trong kho Windows' : 'Chưa có khóa'}</strong></div></div>
    <div className="actions"><button disabled={busy || !settings || (platform !== 'UNCONFIGURED' && !modelId.trim())} onClick={() => work(() => api('/settings/media', { expected_revision: settings!.revision, platform, model_id: modelId.trim() }), 'Đã lưu cấu hình; API video vẫn chưa mở live.')}>Lưu cấu hình video</button><button disabled={busy || !settings || settings.platform === 'UNCONFIGURED' || platform !== settings.platform || modelId !== settings.model_id} onClick={() => { setMessage('Đang mở cửa sổ Windows; kiểm tra thanh tác vụ nếu chưa thấy.'); work(() => api('/settings/media/key/prompt', {}), 'Đã lưu khóa vào kho Windows. Chưa gọi API có phí.') }}>Nhập khóa trong cửa sổ Windows</button><button disabled={busy || !settings?.key_configured} onClick={() => work(() => api('/settings/media/key/delete', {}), 'Đã xóa khóa của nền tảng đã lưu.')}>Xóa khóa video</button><button disabled={busy} onClick={() => work(() => api('/settings/media'), 'Đã tải lại cấu hình.')}>Tải lại</button></div>
    {error && <div className="alert error" role="alert">{error}</div>}{message && <p role="status" className="help">{message}</p>}
    {settings?.blockers.map(item => <p className="help" key={item}>{item}</p>)}
    <p className="help">Preset mục tiêu: nháp 480p, final 720p. Hiện chưa có adapter gửi request video thật hoặc worker Foley. Các lựa chọn được lưu để chuẩn bị kiểm chứng; không xác nhận chất lượng, chi phí hay Unreal.</p>
  </section>
}
