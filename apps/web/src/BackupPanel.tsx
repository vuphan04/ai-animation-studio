import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import { errorLabel, stateLabel } from './uiText'
import { t } from './i18n'

type Settings = { revision: number; backup_parent: string; restore_parent: string }
type RecoveryJob = { id: string; kind: 'BACKUP' | 'RESTORE'; state: string; error: string | null; created_at: string; result: { bundle?: string; root?: string; scope?: string; file_count?: number; integrity?: string } | null }

const scopeLabel: Record<string, string> = { DB_ALL_PROJECT_FILES_AND_UE: t('dữ liệu app, mọi thư mục dự án và project Unreal'), LOCAL_ONLY_NO_UE: t('dữ liệu app và mọi thư mục dự án (chưa cấu hình project Unreal)') }
const stamp = (value: string) => value.slice(0, 16).replace('T', ' ')

export default function BackupPanel() {
  const [saved, setSaved] = useState<Settings>({ revision: 0, backup_parent: '', restore_parent: '' })
  const [settings, setSettings] = useState({ backup_parent: '', restore_parent: '' })
  const savedRef = useRef(saved)
  const [jobs, setJobs] = useState<RecoveryJob[]>([])
  const [closed, setClosed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const reload = useCallback(async () => {
    const [value, list] = await Promise.all([api<Settings>('/settings/recovery'), api<RecoveryJob[]>('/recovery/jobs')])
    const previous = savedRef.current
    savedRef.current = value; setSaved(value); setJobs(list)
    // A background refresh never erases folders the operator has picked but not saved yet.
    setSettings(draft => draft.backup_parent === previous.backup_parent && draft.restore_parent === previous.restore_parent ? { backup_parent: value.backup_parent, restore_parent: value.restore_parent } : draft)
  }, [])
  useEffect(() => { reload().catch(e => setError(errorLabel(e))) }, [reload])
  useEffect(() => {
    if (!jobs.some(j => ['QUEUED', 'RUNNING'].includes(j.state))) return
    const timer = window.setInterval(() => reload().catch(e => setError(errorLabel(e))), 2500)
    return () => window.clearInterval(timer)
  }, [jobs, reload])
  async function work(action: () => Promise<unknown>, text: string) {
    setBusy(true); setError(''); setNotice('')
    try { await action(); await reload(); setNotice(text) } catch (e) { setError(errorLabel(e)) } finally { setBusy(false) }
  }
  // Jobs run on the saved folders, so nothing starts while the fields show something else.
  const unsaved = settings.backup_parent !== saved.backup_parent || settings.restore_parent !== saved.restore_parent
  const save = () => work(async () => {
    const value = await api<Settings>('/settings/recovery', { expected_revision: saved.revision, backup_parent: settings.backup_parent, restore_parent: settings.restore_parent })
    savedRef.current = value; setSaved(value); setSettings({ backup_parent: value.backup_parent, restore_parent: value.restore_parent })
  }, t('Đã lưu hai thư mục; cấu hình được giữ sau khi khởi động lại.'))
  const browse = (field: 'backup_parent' | 'restore_parent') => work(async () => { const picked = await api<{ path: string }>('/settings/recovery/browse', {}); setSettings(s => ({ ...s, [field]: picked.path })) }, t('Đã chọn thư mục. Bấm Lưu thư mục.'))

  return <section className="panel"><div className="panel-heading"><div><h3>{t('Sao lưu và phục hồi')}</h3><p>{t('Một bản sao lưu gồm dữ liệu của app, mọi thư mục dự án và project Unreal đã cấu hình. Phục hồi ghi ra một thư mục mới để bạn kiểm tra; app vẫn chạy trên dữ liệu hiện tại.')}</p></div><span className={`status ${saved.revision && !unsaved ? 'success' : 'neutral'}`}>{unsaved ? t('Chưa lưu') : saved.revision ? t('Đã lưu thư mục') : t('Chưa chọn thư mục')}</span></div>
    {error && <div className="alert error" role="alert">{error}</div>}
    {notice && <div className="alert notice" role="status">{notice}</div>}
    <div className="settings-fields">{(['backup_parent', 'restore_parent'] as const).map(field => <div className="setting-file" key={field}><label className="field grow">{field === 'backup_parent' ? t('Thư mục chứa bản sao lưu') : t('Thư mục chứa bản phục hồi')}<input value={settings[field]} onChange={e => setSettings(s => ({ ...s, [field]: e.target.value }))} placeholder={t('Thư mục nằm ngoài thư mục dữ liệu của app — bấm Duyệt…')} spellCheck={false} /></label><button disabled={busy} onClick={() => browse(field)}>{t('Duyệt…')}</button></div>)}</div>
    <label className="acceptance-confirm"><input type="checkbox" checked={closed} onChange={e => setClosed(e.target.checked)} />{t('Tôi đã đóng Unreal và các ứng dụng đang sửa media của dự án.')}</label>
    <div className="actions"><button disabled={busy || !unsaved || !settings.backup_parent || !settings.restore_parent} onClick={save}>{t('Lưu thư mục')}</button>
      <button className="button-primary" disabled={busy || !closed || !saved.revision || unsaved} onClick={() => work(() => api('/recovery/jobs', { kind: 'BACKUP', ue_closed: closed }), t('Đã xếp việc sao lưu. Trong lúc sao lưu, app tạm không nhận thay đổi.'))}>{t('Sao lưu ngay')}</button></div>
    {unsaved && <p className="help">{t('Thư mục đang hiển thị chưa được lưu. Bấm Lưu thư mục trước khi sao lưu hoặc phục hồi.')}</p>}
    {jobs.length > 0 && <div className="table-wrap"><table><thead><tr><th>{t('Việc')}</th><th>{t('Trạng thái')}</th><th>{t('Kết quả')}</th><th></th></tr></thead><tbody>{jobs.map(job => <tr key={job.id}>
      <td className="strong">{job.kind === 'BACKUP' ? t('Sao lưu') : t('Phục hồi')} · {stamp(job.created_at)}</td>
      <td><span className={`status ${job.state === 'COMPLETED' ? 'success' : ['QUEUED', 'RUNNING'].includes(job.state) ? 'neutral' : 'warning'}`}>{stateLabel(job.state)}</span></td>
      <td>{job.error ?? (job.result ? <>{job.kind === 'BACKUP' ? `${job.result.file_count} file · ${scopeLabel[job.result.scope ?? ''] ?? job.result.scope}` : t('Kiểm tra dữ liệu: {0}', job.result.integrity === 'ok' ? t('đạt') : job.result.integrity)}<br /><code>{job.result.bundle ?? job.result.root}</code></> : '—')}</td>
      <td>{job.kind === 'BACKUP' && job.state === 'COMPLETED' && <button disabled={busy || !closed || unsaved} onClick={() => work(() => api('/recovery/jobs', { kind: 'RESTORE', backup_id: job.id, ue_closed: closed }), t('Đã xếp việc phục hồi vào thư mục mới.'))}>{t('Phục hồi bản này')}</button>}</td>
    </tr>)}</tbody></table></div>}
    <p className="help">{t('Sao lưu chỉ chạy khi không còn việc nào đang chờ, đang chạy hoặc chưa đối soát. Khóa API nằm trong kho bảo mật Windows và không có trong bản sao lưu. Bản phục hồi đã qua kiểm tra checksum và toàn vẹn dữ liệu, nhưng chưa được mở thử trong Unreal hay DaVinci Resolve.')}</p>
  </section>
}
