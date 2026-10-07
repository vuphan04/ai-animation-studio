import { useEffect, useState } from 'react'
import { api } from './api'
import { errorLabel } from './uiText'
import { t } from './i18n'

type Voice = { revision: number; provider: string; model_id: string; output_format: string; output_formats: { id: string; sample_rate: number }[]; key_configured: boolean; configured: boolean; voice_count: number; catalog_fetched_at: string | null; models: { model_id: string; name: string; languages: string[] }[] }

export default function VoiceSettingsPanel() {
  const [saved, setSaved] = useState<Voice | null>(null)
  const [provider, setProvider] = useState('UNCONFIGURED')
  const [model, setModel] = useState('')
  const [format, setFormat] = useState('pcm_24000')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  function apply(value: Voice) { setSaved(value); setProvider(value.provider); setModel(value.model_id); setFormat(value.output_format) }
  useEffect(() => { api<Voice>('/settings/voice').then(apply).catch(e => setError(errorLabel(e))) }, [])
  async function work(action: () => Promise<Voice>, result: string) {
    setBusy(true); setError(''); setMessage('')
    try { apply(await action()); setMessage(result) } catch (e) { setError(errorLabel(e)) } finally { setBusy(false) }
  }
  return <section className="panel"><div className="panel-heading"><div><h3>{t('Giọng thoại · ElevenLabs')}</h3><p>{t('Khóa được nhập trong cửa sổ bảo mật Windows. Danh sách giọng dùng để gán giọng cho từng nhân vật trong Style bible; tải danh sách không tạo âm thanh. Thoại được tạo ở bước C4 của từng tập.')}</p></div><span className={`status ${saved?.configured ? 'success' : 'neutral'}`}>{saved?.configured ? t('Đã lưu') : t('Chưa cấu hình')}</span></div>
    <div className="planner-fields">
      <label className="field">{t('Nhà cung cấp')}<select value={provider} onChange={e => setProvider(e.target.value)}><option value="UNCONFIGURED">{t('Chưa chọn')}</option><option value="ELEVENLABS">ElevenLabs</option></select></label>
      <label className="field">{t('Model tạo thoại')}<select value={model} disabled={provider !== 'ELEVENLABS'} onChange={e => setModel(e.target.value)}><option value="">{t('Chọn trước khi tạo thoại (C4)')}</option>{saved?.models.map(m => <option key={m.model_id} value={m.model_id}>{m.name}{m.languages.includes('vi') ? t(' · có tiếng Việt') : ''}</option>)}</select></label>
      <label className="field">{t('Chất lượng âm thanh nhận về')}<select value={format} onChange={e => setFormat(e.target.value)}>{saved?.output_formats.map(f => <option key={f.id} value={f.id}>PCM {f.sample_rate / 1000} kHz{f.id === 'pcm_44100' ? t(' · cần gói ElevenLabs hỗ trợ') : ''}</option>)}</select></label>
      <div className="field"><span>{t('Trạng thái')}</span><strong>{saved?.key_configured ? t('Đã có khóa trong kho Windows') : t('Chưa có khóa')} · {saved?.voice_count ?? 0}{t(' giọng đã tải')}</strong></div>
    </div>
    <div className="actions">
      <button className="button-primary" disabled={busy || !saved} onClick={() => work(() => api('/settings/voice', { expected_revision: saved!.revision, provider, model_id: provider === 'ELEVENLABS' ? model : '', output_format: format }), t('Đã lưu cấu hình giọng. Chưa gửi yêu cầu tạo thoại.'))}>{t('Lưu cấu hình giọng')}</button>
      <button disabled={busy} onClick={() => { setMessage(t('Đang mở cửa sổ Windows; kiểm tra thanh tác vụ nếu chưa thấy.')); work(() => api('/settings/voice/key/prompt', {}), t('Đã lưu khóa ElevenLabs vào kho Windows. Giá trị khóa không đi qua trang web.')) }}>{t('Nhập khóa trong cửa sổ Windows')}</button>
      <button disabled={busy || !saved?.key_configured} onClick={() => work(() => api('/settings/voice/catalog/refresh', {}), t('Đã tải danh sách giọng và model từ ElevenLabs.'))}>{t('Tải danh sách giọng')}</button>
      <button disabled={busy || !saved?.key_configured} onClick={() => work(() => api('/settings/voice/key/delete', {}), t('Đã xóa khóa ElevenLabs.'))}>{t('Xóa khóa')}</button>
      <button className="button-quiet" disabled={busy} onClick={() => work(() => api('/settings/voice'), t('Đã tải lại cấu hình.'))}>{t('Tải lại')}</button>
    </div>
    {error && <div className="alert error settings-result" role="alert">{error}</div>}
    {message && <div className="preflight ready settings-result" role="status">{message}</div>}
    <p className="help">{saved?.catalog_fetched_at ? t('Danh sách tải lúc {0}. ', saved.catalog_fetched_at.slice(0, 16).replace('T', ' ')) : ''}{t('Trạng thái này chỉ xác nhận cấu hình và danh sách giọng, không chứng minh ElevenLabs đã tạo được thoại. Mỗi yêu cầu tạo thoại ở C4 đi qua trần chi phí tháng; đổi chất lượng âm thanh giữa chừng thì các take của một tập phải tạo lại cho thống nhất.')}</p>
  </section>
}
