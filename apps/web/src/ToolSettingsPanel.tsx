import { useEffect, useState } from 'react'
import { api } from './api'
import { errorLabel } from './uiText'
import { t } from './i18n'

type Tools = { revision: number; ffmpeg_path: string; configured: boolean; version: string | null; ffprobe_path: string | null; error: string | null; detected_path: string }

export default function ToolSettingsPanel() {
  const [saved, setSaved] = useState<Tools | null>(null)
  const [path, setPath] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  function apply(value: Tools) { setSaved(value); setPath(value.ffmpeg_path) }
  useEffect(() => { api<Tools>('/settings/tools').then(apply).catch(e => setError(errorLabel(e))) }, [])
  async function work(action: () => Promise<void>) {
    setBusy(true); setError(''); setMessage('')
    try { await action() } catch (e) { setError(errorLabel(e)) } finally { setBusy(false) }
  }
  const changed = Boolean(saved && path.trim() !== saved.ffmpeg_path)
  return <section className="panel"><div className="panel-heading"><div><h3>{t('Công cụ xử lý video · FFmpeg')}</h3><p>{t('Dùng cho chuẩn hóa shot, ghép rough cut, MP4 preview và QC ở các bước sau. Chọn tệp ffmpeg.exe có ffprobe.exe nằm cùng thư mục.')}</p></div><span className={`status ${saved?.error ? 'warning' : changed || !saved?.configured ? 'neutral' : 'success'}`}>{changed ? t('Chưa lưu') : saved?.error ? t('Cần sửa cấu hình') : saved?.configured ? t('Đã lưu') : t('Chưa cấu hình')}</span></div>
    <div className="settings-fields"><div className="setting-file"><label className="field grow">{t('Tệp ffmpeg.exe')}<input value={path} disabled={busy} onChange={e => setPath(e.target.value)} placeholder={t('Chưa chọn ffmpeg.exe — bấm Duyệt…')} spellCheck={false} /></label><button type="button" disabled={busy} onClick={() => work(async () => { setMessage(t('Đang mở hộp thoại chọn tệp Windows…')); const picked = await api<{ path: string | null }>('/settings/tools/browse', {}); if (picked.path) { setPath(picked.path); setMessage(t('Đã chọn tệp. Bấm Lưu cấu hình.')) } else setMessage(t('Đã hủy chọn tệp.')) })}>{t('Duyệt…')}</button></div></div>
    {saved && !saved.ffmpeg_path && saved.detected_path && <p className="help">{t('Tìm thấy FFmpeg trên máy: ')}<code>{saved.detected_path}</code> <button type="button" className="button-quiet" disabled={busy} onClick={() => setPath(saved.detected_path)}>{t('Dùng đường dẫn này')}</button></p>}
    <div className="actions"><button disabled={busy || !path.trim()} onClick={() => work(async () => { const result = await api<{ version: string; ffprobe_path: string | null }>('/settings/tools/validate', { ffmpeg_path: path }); setMessage(t('FFmpeg {0} trả lời hợp lệ{1}', result.version, result.ffprobe_path ? t('; có ffprobe.') : t('; thiếu ffprobe.exe cạnh ffmpeg.exe.'))) })}>{t('Kiểm tra')}</button><button className="button-primary" disabled={busy || !saved || !path.trim()} onClick={() => work(async () => { apply(await api<Tools>('/settings/tools', { expected_revision: saved!.revision, ffmpeg_path: path })); setMessage(t('Đã lưu FFmpeg trên máy này; cấu hình được giữ sau khi khởi động lại.')) })}>{t('Lưu cấu hình')}</button><button className="button-quiet" disabled={busy} onClick={() => work(async () => { apply(await api<Tools>('/settings/tools')); setMessage(t('Đã tải cấu hình đang lưu.')) })}>{t('Tải lại')}</button></div>
    {error && <div className="alert error settings-result" role="alert">{error}</div>}
    {message && <div className="preflight ready settings-result" role="status">{message}</div>}
    {saved?.error && <div className="preflight blocked settings-result">{t('Cấu hình đã lưu cần sửa: ')}{saved.error}</div>}
    {saved?.configured && <p className="help">{t('Phiên bản đang dùng: FFmpeg ')}{saved.version}{t('. Trạng thái này chỉ xác nhận tệp chạy được, chưa xác nhận một lần xuất video.')}</p>}
  </section>
}
