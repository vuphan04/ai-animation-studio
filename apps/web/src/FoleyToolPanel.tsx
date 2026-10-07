import { useEffect, useState } from 'react'
import { api } from './api'
import { errorLabel } from './uiText'
import { t } from './i18n'

type Field = 'python_path' | 'script_path' | 'model_path' | 'config_path'
type FoleyTool = { revision: number; configured: boolean; error: string | null; note: string } & Record<Field, string>

const fields: [Field, string, string][] = [
  ['python_path', t('python.exe của môi trường đã cài HunyuanVideo-Foley'), t('Ví dụ: …\\envs\\foley\\python.exe')],
  ['script_path', t('File infer.py trong thư mục HunyuanVideo-Foley'), t('Ví dụ: …\\HunyuanVideo-Foley\\infer.py')],
  ['model_path', t('Thư mục model đã tải về'), t('Ví dụ: …\\HunyuanVideo-Foley\\pretrained')],
  ['config_path', t('File cấu hình model (.yaml)'), t('Ví dụ: …\\configs\\hunyuanvideo-foley-xxl.yaml')],
]
const empty: Record<Field, string> = { python_path: '', script_path: '', model_path: '', config_path: '' }

export default function FoleyToolPanel() {
  const [saved, setSaved] = useState<FoleyTool | null>(null)
  const [paths, setPaths] = useState<Record<Field, string>>(empty)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  function apply(value: FoleyTool) { setSaved(value); setPaths({ python_path: value.python_path, script_path: value.script_path, model_path: value.model_path, config_path: value.config_path }) }
  useEffect(() => { api<FoleyTool>('/settings/foley-tool').then(apply).catch(e => setError(errorLabel(e))) }, [])
  async function work(action: () => Promise<void>) {
    setBusy(true); setError(''); setMessage('')
    try { await action() } catch (e) { setError(errorLabel(e)) } finally { setBusy(false) }
  }
  const changed = Boolean(saved && fields.some(([key]) => paths[key].trim() !== saved[key]))
  const complete = fields.every(([key]) => paths[key].trim())
  return <section className="panel"><div className="panel-heading"><div><h3>Foley local · HunyuanVideo-Foley</h3><p>{t('Dùng ở bước 12 để tạo Foley cho từng shot trên máy này. Cài HunyuanVideo-Foley theo hướng dẫn của công cụ, rồi chọn bốn đường dẫn dưới đây. Foley xếp hàng chung GPU với Unreal nên không chạy cùng lúc với render.')}</p></div><span className={`status ${saved?.error ? 'warning' : changed || !saved?.configured ? 'neutral' : 'success'}`}>{changed ? t('Chưa lưu') : saved?.error ? t('Cần sửa cấu hình') : saved?.configured ? t('Đã lưu') : t('Chưa cấu hình')}</span></div>
    <div className="settings-fields">{fields.map(([key, label, hint]) => <div key={key} className="setting-file"><label className="field grow">{label}<input value={paths[key]} disabled={busy} onChange={e => setPaths({ ...paths, [key]: e.target.value })} placeholder={hint} spellCheck={false} /></label><button type="button" disabled={busy} onClick={() => work(async () => { setMessage(t('Đang mở hộp thoại chọn của Windows…')); const picked = await api<{ path: string | null }>('/settings/foley-tool/browse', { field: key, current: paths[key] }); if (picked.path) { setPaths(current => ({ ...current, [key]: picked.path! })); setMessage(t('Đã chọn. Bấm Lưu cấu hình khi đủ bốn mục.')) } else setMessage(t('Đã hủy chọn.')) })}>{t('Duyệt…')}</button></div>)}</div>
    <div className="actions"><button className="button-primary" disabled={busy || !saved || !complete} onClick={() => work(async () => { apply(await api<FoleyTool>('/settings/foley-tool', { expected_revision: saved!.revision, ...paths })); setMessage(t('Đã lưu cấu hình Foley trên máy này; cấu hình được giữ sau khi khởi động lại.')) })}>{t('Lưu cấu hình')}</button><button className="button-quiet" disabled={busy} onClick={() => work(async () => { apply(await api<FoleyTool>('/settings/foley-tool')); setMessage(t('Đã tải cấu hình đã lưu.')) })}>{t('Tải lại')}</button></div>
    {error && <div className="alert error settings-result" role="alert">{error}</div>}
    {message && <div className="preflight ready settings-result" role="status">{message}</div>}
    {saved?.error && <div className="preflight blocked settings-result">{t('Cấu hình đã lưu cần sửa: ')}{saved.error}</div>}
    <p className="help">{saved?.note}{t(' Chưa cài được model thì vẫn làm được bước 12: nhập Foley làm tay cho từng shot ở tab Final & xuất phim.')}</p>
  </section>
}
