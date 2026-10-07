import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { errorLabel } from './uiText'
import { t } from './i18n'

type Estimate = { generate_seconds: number | null; draft_micro: number; final_micro: number; input_micro: number; video_micro: number; video_currency: string | null; voice_micro: number; voice_currency: string }
type RouteShot = { shot_id: string; description: string; shot_size: string; duration_seconds: number; face_visible: boolean; content_flags: string[]; has_dialogue: boolean; suggestion: { route: string; video_model: string | null; reasons: string[]; warnings: string[] }; route: string; video_model: string | null; locked: boolean; decision: { revision: number; locked: boolean; stale: boolean } | null; estimate: Estimate; issues: string[]; warnings: string[]; notes: string[]; needs_kimodo: boolean }
type Total = { currency: string; video_micro: number; voice_micro: number; total_micro: number; budget: { allowed: boolean; reason: string | null; near_cap?: boolean } }
type Plan = { ready: boolean; blockers: string[]; default_video_model: string; settings_saved: boolean; scenes: { scene_key: string; heading: string; shots: RouteShot[] }[]; totals: Total[]; counts: Record<string, number>; plan_hash: string | null; approve_blockers: string[]; allowed: boolean; c3: { sequence: number; created_at: string; current: boolean } | null; bulk?: { locked: number; skipped: string[] } }
type ModelPrice = { currency: string; draft_micro_per_second: number; final_micro_per_second: number; input_video_micro_per_second: number }
type Prices = { video: Record<string, ModelPrice>; voice: { currency: string; micro_per_1k_chars: number } }
type Settings = { revision: number; main_characters: string[]; prices: Prices; saved: boolean; characters: { entity_key: string; display_name: string }[]; suggested_prices: Prices; models: { id: string; label: string; min_seconds: number; max_seconds: number | null }[]; price_note: string }

const routeName: Record<string, string> = { UE: t('UE · dựng hoàn toàn Unreal'), AI: t('AI · tạo video trực tiếp'), HYBRID: t('Hybrid · nguồn UE + AI') }
const routeShort: Record<string, string> = { UE: 'UE', AI: 'AI', HYBRID: 'Hybrid' }
const sizes: Record<string, string> = { EXTREME_WIDE: t('toàn cảnh rất rộng'), WIDE: t('toàn cảnh'), MEDIUM: t('trung cảnh'), CLOSE_UP: t('cận cảnh'), EXTREME_CLOSE_UP: t('đặc tả'), OVER_SHOULDER: 'qua vai', TWO_SHOT: t('hai nhân vật'), INSERT: t('chèn chi tiết') }
const money = (micro: number, currency: string) => `${(micro / 1e6).toFixed(2)} ${currency}`
const units = (micro: number) => micro / 1e6
const micro = (value: string) => Math.max(0, Math.round(Number(value) * 1e6) || 0)

export default function RoutePanel({ projectId, episodeId, refreshKey, onChanged }: { projectId: string; episodeId: string; refreshKey: string; onChanged: () => void }) {
  const [plan, setPlan] = useState<Plan | null>(null)
  const [settings, setSettings] = useState<Settings | null>(null)
  const [main, setMain] = useState<string[]>([])
  const [prices, setPrices] = useState<Prices | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const applySettings = (value: Settings) => { setSettings(value); setMain(value.main_characters); setPrices(value.saved ? value.prices : value.suggested_prices) }
  const load = useCallback(async () => {
    const [nextPlan, nextSettings] = await Promise.all([api<Plan>(`/episodes/${episodeId}/routes`), api<Settings>(`/projects/${projectId}/route-settings`)])
    setPlan(nextPlan); applySettings(nextSettings)
  }, [episodeId, projectId])
  useEffect(() => { setError(''); setNotice(''); load().catch(e => setError(errorLabel(e))) }, [load, refreshKey])

  async function act(action: () => Promise<Plan | void>, message: string | ((result: Plan | void) => string)) {
    setBusy(true); setError(''); setNotice('')
    try { const result = await action(); if (result) setPlan(result); else await load(); setNotice(typeof message === 'string' ? message : message(result)); onChanged() } catch (e) { setError(errorLabel(e)) } finally { setBusy(false) }
  }
  const decide = (shot: RouteShot, route: string, model: string | null, locked: boolean) => api<Plan>(`/episodes/${episodeId}/routes/decide`, { shot_id: shot.shot_id, expected_revision: shot.decision?.revision ?? 0, route, video_model: route === 'UE' ? null : model, locked })
  const setPrice = (model: string, change: Partial<ModelPrice>) => prices && setPrices({ ...prices, video: { ...prices.video, [model]: { ...prices.video[model], ...change } } })

  if (!plan || !settings || !prices) return <section className="panel"><div className="panel-heading"><div><h3>{t('05 / C3 · Phân loại shot, chi phí, bạn duyệt')}</h3><p>{t('Đang tải…')}</p></div></div>{error && <div className="alert error" role="alert">{error}</div>}</section>
  const modelLabel = (id: string | null) => settings.models.find(m => m.id === id)?.label ?? '—'

  return <section className="panel"><div className="panel-heading"><div><h3>{t('05 / C3 · Phân loại shot, chi phí, bạn duyệt')}</h3><p>{t('App đề xuất UE / AI / Hybrid theo quy tắc cứng và hiện chi phí dự kiến từng shot. Bạn đổi, khóa phương thức từng shot rồi chốt cả tập qua cổng ngân sách ①.')}</p></div><span className={`status ${plan.c3?.current ? 'success' : plan.allowed ? 'neutral' : 'warning'}`}>{plan.c3?.current ? t('Đã chốt · lần {0}', plan.c3.sequence) : plan.c3 ? t('Đã đổi sau lần chốt') : t('Chưa chốt')}</span></div>
    {error && <div className="alert error" role="alert">{error}</div>}
    {notice && <div className="alert notice" role="status">{notice}</div>}
    {!plan.ready && <div className="preflight blocked settings-result">{plan.blockers.join(' ')}{t(' Hoàn tất các bước phía trên trước khi phân loại shot.')}</div>}

    <details className="scene-card" open={!settings.saved}><summary><strong>{t('Bảng giá và nhân vật chính · dùng chung cho cả series')}</strong><span className={`status ${settings.saved ? 'success' : 'warning'}`}>{settings.saved ? t('Đã lưu · bản {0}', settings.revision) : t('Chưa lưu')}</span></summary>
      <h5>{t('Nhân vật chính')}</h5>
      <div className="actions">{settings.characters.map(c => <label key={c.entity_key} className="acceptance-confirm"><input type="checkbox" checked={main.includes(c.entity_key)} onChange={e => setMain(e.target.checked ? [...main, c.entity_key] : main.filter(k => k !== c.entity_key))} />{c.display_name}</label>)}</div>
      <p className="help">{t('Quy tắc “thoại quay cận mặt nhân vật chính → UE” dùng danh sách này. Chưa đánh dấu ai thì mọi nhân vật có thoại đều được coi là nhân vật chính.')}</p>
      <h5>{t('Giá video AI (mỗi giây tạo)')}</h5>
      <div className="table-wrap"><table><thead><tr><th>Model</th><th>{t('Đơn vị tiền')}</th><th>Draft</th><th>Final</th><th>{t('Video đầu vào (Hybrid)')}</th><th>Clip</th></tr></thead><tbody>{settings.models.map(m => { const p = prices.video[m.id]; return <tr key={m.id}><td className="strong">{m.label}</td><td><select value={p.currency} onChange={e => setPrice(m.id, { currency: e.target.value })} aria-label={t('Đơn vị tiền của {0}', m.label)}><option value="USD">USD</option><option value="CNY">CNY</option></select></td><td><input type="number" min={0} step={0.01} value={units(p.draft_micro_per_second)} onChange={e => setPrice(m.id, { draft_micro_per_second: micro(e.target.value) })} aria-label={t('Giá draft của {0}', m.label)} /></td><td><input type="number" min={0} step={0.01} value={units(p.final_micro_per_second)} onChange={e => setPrice(m.id, { final_micro_per_second: micro(e.target.value) })} aria-label={t('Giá final của {0}', m.label)} /></td><td><input type="number" min={0} step={0.01} value={units(p.input_video_micro_per_second)} onChange={e => setPrice(m.id, { input_video_micro_per_second: micro(e.target.value) })} aria-label={t('Giá video đầu vào của {0}', m.label)} /></td><td>{t('tối thiểu ')}{m.min_seconds}{t(' giây')}{m.max_seconds ? t(' · tối đa {0} giây', m.max_seconds) : ''}</td></tr> })}</tbody></table></div>
      <div className="form-row"><label className="field">{t('Giá thoại ElevenLabs mỗi 1.000 ký tự')}<input type="number" min={0} step={0.01} value={units(prices.voice.micro_per_1k_chars)} onChange={e => setPrices({ ...prices, voice: { ...prices.voice, micro_per_1k_chars: micro(e.target.value) } })} /></label><label className="field">{t('Đơn vị tiền')}<select value={prices.voice.currency} onChange={e => setPrices({ ...prices, voice: { ...prices.voice, currency: e.target.value } })}><option value="USD">USD</option><option value="CNY">CNY</option></select></label><button className="button-primary" disabled={busy} onClick={() => act(async () => { applySettings(await api<Settings>(`/projects/${projectId}/route-settings`, { expected_revision: settings.revision, main_characters: main, prices })) }, t('Đã lưu bảng giá và nhân vật chính. Chi phí dự kiến được tính lại.'))}>{t('Lưu bảng giá và nhân vật chính')}</button></div>
      <p className="help">{settings.price_note} {!settings.saved && t('Số đang hiện cho MiniMax H3 là gợi ý từ ghi chú 03/10/2026 của sơ đồ (draft 768P 0,08; final = regenerate 0,05 + phí video gốc 0,05; video đầu vào 0,05 USD/giây) và chưa được lưu. Sơ đồ không ghi giá Seedance 2.5: nhập theo bảng giá tài khoản của bạn. ')}{t('Giá thoại nhập 0 nếu đã nằm trong gói thuê bao. Đổi giá sau khi chốt C3 thì phải chốt lại.')}</p>
    </details>

    {plan.ready && <>
      <div className="info-line"><span>{plan.counts.shots} shot · UE {plan.counts.UE} · AI {plan.counts.AI} · Hybrid {plan.counts.HYBRID}{plan.counts.kimodo ? t(' · {0} shot cần Kimodo ở bước 08', plan.counts.kimodo) : ''}</span><strong>{t('Đã khóa ')}{plan.counts.locked}/{plan.counts.shots}</strong></div>
      {plan.totals.length ? plan.totals.map(entry => <div key={entry.currency} className={`preflight ${entry.budget.allowed ? 'ready' : 'blocked'} settings-result`}><strong>{t('Cổng ngân sách ① · ')}{entry.currency}:</strong>{t(' chi phí dự kiến của tập ')}{money(entry.total_micro, entry.currency)}{t(' (video AI ')}{money(entry.video_micro, entry.currency)}{t(' · thoại ')}{money(entry.voice_micro, entry.currency)}). {entry.budget.allowed ? (entry.budget.near_cap ? t('Còn trong trần nhưng đã chạm ngưỡng cảnh báo của tháng.') : t('Còn trong trần chi phí tháng.')) : entry.budget.reason}</div>) : <p className="help">{t('Tập này chưa có khoản phí dự kiến nào (toàn shot UE và giá thoại bằng 0).')}</p>}
      {plan.scenes.map(scene => <details key={scene.scene_key} className="scene-card"><summary><strong>{scene.scene_key} · {scene.heading}</strong><span className="muted">{scene.shots.map(s => routeShort[s.route]).join(' · ')}</span><span className={`status ${scene.shots.every(s => s.locked) ? 'success' : scene.shots.some(s => s.issues.length) ? 'warning' : 'neutral'}`}>{scene.shots.every(s => s.locked) ? t('Đã khóa cả scene') : t('Đã khóa {0}/{1}', scene.shots.filter(s => s.locked).length, scene.shots.length)}</span></summary>
        {scene.shots.map(shot => <div key={shot.shot_id} className="route-shot">
          <div className="route-shot-head"><strong>{shot.shot_id}</strong><span className="muted">{sizes[shot.shot_size]} · {shot.duration_seconds}{t(' giây dự kiến · ')}{shot.face_visible ? t('lộ mặt') : t('không lộ mặt')}{shot.has_dialogue ? t(' · có thoại') : ''}{shot.content_flags.length ? t(' · bạo lực/máu me') : ''}</span><span className={`status ${shot.locked ? 'success' : shot.issues.length || shot.decision?.stale ? 'warning' : 'neutral'}`}>{shot.locked ? t('Đã khóa') : shot.decision?.stale ? t('Shot đã đổi · khóa lại') : shot.issues.length ? t('Chưa khóa được') : t('Chưa khóa')}</span></div>
          <p className="help">{shot.description}</p>
          <p className="help"><strong>{t('Đề xuất ')}{routeShort[shot.suggestion.route]}:</strong> {shot.suggestion.reasons.join(' ')} {shot.suggestion.warnings.join(' ')}</p>
          <div className="form-row"><label className="field">{t('Phương thức')}<select value={shot.route} disabled={busy || shot.locked} onChange={e => act(() => decide(shot, e.target.value, shot.video_model ?? plan.default_video_model, false), t('Đã đổi phương thức {0}; chưa khóa.', shot.shot_id))}>{Object.entries(routeName).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            {shot.route !== 'UE' && <label className="field">{t('Model video')}<select value={shot.video_model ?? ''} disabled={busy || shot.locked} onChange={e => act(() => decide(shot, shot.route, e.target.value, false), t('Đã đổi model {0}; chưa khóa.', shot.shot_id))}>{settings.models.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}</select></label>}
            <div className="field grow"><span>{t('Chi phí dự kiến')}</span><strong>{shot.estimate.video_currency ? t('{0} · {1} tạo {2} giây (draft {3} + final {4}{5})', money(shot.estimate.video_micro, shot.estimate.video_currency), modelLabel(shot.video_model), shot.estimate.generate_seconds, money(shot.estimate.draft_micro, shot.estimate.video_currency), money(shot.estimate.final_micro, shot.estimate.video_currency), shot.estimate.input_micro ? t(' + video đầu vào {0}', money(shot.estimate.input_micro, shot.estimate.video_currency)) : '') : t('Video: không phí cloud')}{shot.estimate.voice_micro ? t(' · thoại {0}', money(shot.estimate.voice_micro, shot.estimate.voice_currency)) : ''}</strong></div>
            {shot.locked ? <button disabled={busy} onClick={() => act(() => decide(shot, shot.route, shot.video_model, false), t('Đã mở khóa {0}.', shot.shot_id))}>{t('Mở khóa')}</button> : <button className="button-primary" disabled={busy || shot.issues.length > 0} onClick={() => act(() => decide(shot, shot.route, shot.video_model ?? plan.default_video_model, true), t('Đã khóa {0} · {1}.', shot.shot_id, routeShort[shot.route]))}>{t('Khóa phương thức')}</button>}</div>
          {shot.issues.map(i => <p key={i} className="row-error">{i}</p>)}
          {[...shot.warnings, ...shot.notes].map(w => <p key={w} className="help">{w}</p>)}
        </div>)}
        <div className="actions"><button disabled={busy || scene.shots.every(s => s.locked)} onClick={() => act(() => api<Plan>(`/episodes/${episodeId}/routes/lock`, { expected_plan_hash: plan.plan_hash, scene_key: scene.scene_key }), r => t('Đã khóa {0} shot của {1}{2}.', r?.bulk?.locked ?? 0, scene.scene_key, r?.bulk?.skipped.length ? t('; {0} shot còn lỗi nên chưa khóa', r.bulk.skipped.length) : ''))}>{t('Khóa cả scene theo phương thức đang hiện')}</button></div>
      </details>)}
      {plan.approve_blockers.length > 0 && !plan.c3?.current && <div className="preflight blocked settings-result"><strong>{t('Chưa chốt được C3:')}</strong><ul>{plan.approve_blockers.map(b => <li key={b}>{b}</li>)}</ul></div>}
      <div className="actions"><button disabled={busy || plan.counts.locked === plan.counts.shots} onClick={() => act(() => api<Plan>(`/episodes/${episodeId}/routes/lock`, { expected_plan_hash: plan.plan_hash, scene_key: null }), r => t('Đã khóa {0} shot{1}.', r?.bulk?.locked ?? 0, r?.bulk?.skipped.length ? t('; {0} shot còn lỗi nên chưa khóa', r.bulk.skipped.length) : ''))}>{t('Khóa mọi shot không có lỗi')}</button><button className="button-primary" disabled={busy || !plan.allowed || Boolean(plan.c3?.current)} onClick={() => act(() => api<Plan>(`/episodes/${episodeId}/c3/approve`, { expected_hash: plan.plan_hash }), t('Đã chốt C3: phương thức từng shot và chi phí dự kiến của tập.'))}>{t('Chốt phương thức và chi phí (C3)')}</button></div>
      <p className="help">{t('Chi phí là ước tính: mỗi shot AI/Hybrid tính một lần draft và một lần final theo thời lượng tạo, không gồm các lần tạo lại. C3 không giữ tiền và không gửi yêu cầu nào; cổng ② và ③ kiểm tra lại trước khi gửi draft và final. Quy tắc đề xuất Hybrid khi thư viện thiếu VFX là suy luận của app, không ghi trong sơ đồ.')}</p>
    </>}
  </section>
}
