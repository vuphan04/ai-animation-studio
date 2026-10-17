import { useEffect, useState } from 'react'
import { api, bootstrap, type UnrealSettings } from './api'
import { errorLabel } from './uiText'
import buildInfo from '../../../resources/app-build.json'
import { t } from './i18n'

export default function UnrealSettingsPanel({ serverBuild, onConfigured, onServerBuild }: { serverBuild: string | null; onConfigured: (configured: boolean) => void; onServerBuild: (build: string | null) => void }) {
  const [saved, setSaved] = useState<UnrealSettings | null>(null)
  const [editorPath, setEditorPath] = useState('')
  const [projectPath, setProjectPath] = useState('')
  const [checked, setChecked] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  function apply(value: UnrealSettings) { setSaved(value); setEditorPath(value.editor_path); setProjectPath(value.project_path); setChecked(false); onConfigured(value.configured) }
  useEffect(() => { api<UnrealSettings>('/settings/unreal').then(apply).catch(e => setError(errorLabel(e))) }, [])
  const complete = Boolean(editorPath.trim() && projectPath.trim())
  const changed = Boolean(saved && (editorPath.trim() !== saved.editor_path || projectPath.trim() !== saved.project_path))
  const hint = !saved ? t('Chưa tải được cấu hình. Bấm Tải lại để thử lại.')
    : !complete ? t('Chọn đủ tệp Unreal Editor và tệp .uproject để bật nút Lưu cấu hình.')
      : t('Có thể bấm Lưu cấu hình ngay. Ứng dụng tự kiểm tra đường dẫn và plugin trước khi lưu.')
  const edit = (set: (value: string) => void) => (value: string) => { set(value); setChecked(false); setError(''); setNotice('') }

  async function work(start: string, action: () => Promise<void>) {
    setError(''); setNotice(start); setBusy(true)
    try { await action() } catch (e) { setChecked(false); setNotice(''); setError(errorLabel(e)) } finally { setBusy(false) }
  }
  const browse = (kind: 'editor' | 'project') => work(t('Đang mở hộp thoại chọn tệp Windows. Chọn tệp hoặc bấm Hủy để quay lại.'), async () => {
    const picked = await api<{ path: string | null }>('/settings/unreal/browse', { kind })
    if (picked.path) { (kind === 'editor' ? setEditorPath : setProjectPath)(picked.path); setChecked(false); setNotice(t('Đã chọn tệp. Khi đã chọn đủ hai tệp, bấm Lưu cấu hình.')) }
    else setNotice(t('Đã hủy chọn tệp. Đường dẫn hiện tại được giữ nguyên.'))
  })
  const check = () => work(t('Đang kiểm tra đường dẫn và plugin…'), async () => {
    const result = await api<{ message: string }>('/settings/unreal/validate', { editor_path: editorPath, project_path: projectPath })
    setChecked(true); setNotice(result.message)
  })
  const save = () => work(t('Đang kiểm tra và lưu cấu hình…'), async () => {
    apply(await api<UnrealSettings>('/settings/unreal', { editor_path: editorPath, project_path: projectPath, expected_revision: saved!.revision }))
    setNotice(t('Đã lưu cấu hình Unreal trên máy này. Cấu hình được giữ sau khi khởi động lại; việc chạy trên project và asset thật cần kiểm chứng riêng.'))
  })
  const reload = () => work(t('Đang tải cấu hình đã lưu…'), async () => {
    onServerBuild((await bootstrap()).build_id)
    apply(await api<UnrealSettings>('/settings/unreal'))
    setNotice(t('Đã tải cấu hình đang lưu trên máy này.'))
  })

  return <>
    <section className="panel" aria-busy={busy}><div className="panel-heading"><div><h3>{t('Kết nối Unreal trên máy này')}</h3><p>{t('Chọn hai tệp bằng nút Duyệt rồi bấm Lưu cấu hình. Ứng dụng kiểm tra trước khi lưu; nút Kiểm tra đường dẫn dùng để kiểm tra riêng.')}</p></div><span className={`status ${error || saved?.error ? 'warning' : changed || !saved?.configured ? 'neutral' : 'success'}`}>{changed ? t('Chưa lưu') : saved?.error ? t('Cần sửa cấu hình') : saved?.configured ? t('Đã lưu') : t('Chưa cấu hình')}</span></div>
      <p className="help">{t('Bản giao diện: ')}{buildInfo.build_id}{t(' · Máy chủ: ')}{serverBuild ?? t('bản cũ chưa cung cấp phiên bản')}.</p>
      {serverBuild !== buildInfo.build_id && <div className="alert error" role="alert">{t('Máy chủ đang chạy khác phiên bản giao diện. Đóng cửa sổ launcher đang chạy, mở lại Start-Studio rồi tải lại trang để dùng bản cập nhật.')}</div>}
      <div className="settings-fields">
        <div className="setting-file"><label className="field grow">{t('Unreal Editor (UnrealEditor-Cmd.exe hoặc UnrealEditor.exe)')}<input value={editorPath} disabled={busy} onChange={e => edit(setEditorPath)(e.target.value)} placeholder={t('Chưa chọn tệp Unreal Editor — bấm Duyệt…')} aria-describedby="unreal-settings-help" spellCheck={false} /></label><button type="button" aria-label={t('Duyệt tệp Unreal Editor')} disabled={busy} onClick={() => browse('editor')}>{t('Duyệt…')}</button></div>
        <div className="setting-file"><label className="field grow">{t('Project Unreal (.uproject)')}<input value={projectPath} disabled={busy} onChange={e => edit(setProjectPath)(e.target.value)} placeholder={t('Chưa chọn project Unreal — bấm Duyệt…')} aria-describedby="unreal-settings-help" spellCheck={false} /></label><button type="button" aria-label={t('Duyệt project Unreal')} disabled={busy} onClick={() => browse('project')}>{t('Duyệt…')}</button></div>
      </div>
      <p id="unreal-settings-help" className="help">{hint}</p>
      <div className="actions"><button disabled={busy || !complete} onClick={check}>{t('Kiểm tra đường dẫn')}</button><button className="button-primary" disabled={busy || !complete || !saved} title={hint} onClick={save}>{t('Lưu cấu hình')}</button><button className="button-quiet" disabled={busy} onClick={reload}>{t('Tải lại')}</button></div>
      {error && <div className="alert error settings-result" role="alert">{t('Không lưu hoặc kiểm tra được cấu hình: ')}{error}</div>}
      {notice && <div className="preflight ready settings-result" role="status">{notice}</div>}
      {saved?.error && <div className="preflight blocked settings-result">{t('Cấu hình đã lưu cần sửa: ')}{saved.error}</div>}
      {checked && <div className="preflight ready settings-result">{t('Đường dẫn và plugin hợp lệ. Lưu cấu hình để dùng cho công việc Unreal.')}</div>}
      <p className="help">{t('Nguồn hiện tại: ')}{saved?.source === 'APP' ? t('đã lưu trong app') : saved?.editor_path || saved?.project_path ? t('cấu hình môi trường cũ') : t('chưa lưu cấu hình')}{t('. Trạng thái này chưa xác nhận asset, Sequencer hoặc phiên bản Unreal thực tế.')}</p>
    </section>
    <section className="panel settings-guide"><div className="panel-heading"><div><h3>{t('Cần chuẩn bị gì?')}</h3><p>{t('Việc chọn đường dẫn không tự tạo project hay tài nguyên Unreal.')}</p></div></div><ol><li>{t('Chọn Unreal Editor 5.8 đang cài trên máy. App viết cho UE 5.8; phiên bản khác bị chặn khi quét asset và lắp dựng.')}</li><li>{t('Tạo hoặc mở project ')}<code>.uproject</code>{t(' có map, nhân vật và animation thật.')}</li><li>{t('Bật ba plugin: PythonScriptPlugin, EditorScriptingUtilities, SequencerScripting.')}</li><li>{t('Vào Thư viện series để quét asset của project và chọn mapping (CS1); không cần gõ đường dẫn asset.')}</li></ol></section>
  </>
}
