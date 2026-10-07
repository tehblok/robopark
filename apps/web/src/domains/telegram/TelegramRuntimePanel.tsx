import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, request } from '../../api'
import { Alert, Panel } from '../../components/PageShell'
import { Button } from '../../design-system/actions/Button'
import { LoadingState } from '../../design-system/feedback/AsyncState'

type Control = { queries_paused: boolean; deliveries_paused: boolean; revision: number }
type Usage = { user_id: number; username: string; telegram_user_id: number | null; stats: { today: number; month: number; total: number } }
type Client = { control(): Promise<Control>; usage(): Promise<Usage[]>; update(value: Control): Promise<Control> }
const defaultClient: Client = {
  control: () => request('/admin/bot/native/control'),
  usage: () => request('/admin/bot/native/usage'),
  update: value => request('/admin/bot/native/control', { method: 'PUT', body: JSON.stringify(value) }),
}

export function TelegramRuntimePanel({ royal, client = defaultClient }: { royal: boolean; client?: Client }) {
  const [control, setControl] = useState<Control | null>(null)
  const [usage, setUsage] = useState<Usage[]>([])
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const generation = useRef(0)
  const load = useCallback(async () => {
    const current = ++generation.current
    setLoading(true); setError(''); setNotice('')
    try {
      const [nextControl, nextUsage] = await Promise.all([client.control(), client.usage()])
      if (generation.current !== current) return
      setControl(nextControl); setUsage(nextUsage)
    } catch {
      if (generation.current === current) { setControl(null); setUsage([]); setError('Не удалось загрузить статистику и состояние паузы. Повторите обновление.') }
    } finally { if (generation.current === current) setLoading(false) }
  }, [client])
  useEffect(() => { void load(); return () => { generation.current += 1 } }, [load])
  const save = async () => {
    if (!royal || !control || busy) return
    const current = generation.current
    setBusy(true); setError(''); setNotice('')
    try {
      const next = await client.update(control)
      if (generation.current === current) { setControl(next); setNotice('Настройки паузы сохранены.') }
    } catch (caught) {
      if (generation.current === current) setError(caught instanceof ApiError && caught.status === 409 ? 'Настройки уже изменены. Обновите состояние перед сохранением.' : 'Не удалось сохранить паузу. Проверьте состояние и повторите.')
    } finally { if (generation.current === current) setBusy(false) }
  }
  return <Panel title="Пауза и статистика" density="dense" hint="Пауза не отключает управление ботом. Администраторы могут проверять запросы сотрудников во время паузы.">
    {error ? <Alert tone="error">{error}</Alert> : null}
    {notice ? <Alert tone="success">{notice}</Alert> : null}
    {loading ? <LoadingState label="Загружаем статистику и паузу" variant="inline" /> : null}
    {control && !loading ? <div className="rp-telegram-runtime">
      <label><input type="checkbox" checked={control.queries_paused} disabled={!royal || busy} onChange={event => setControl({ ...control, queries_paused: event.target.checked })} />Пауза запросов сотрудников</label>
      <label><input type="checkbox" checked={control.deliveries_paused} disabled={!royal || busy} onChange={event => setControl({ ...control, deliveries_paused: event.target.checked })} />Пауза рассылок</label>
      {royal ? <Button busy={busy} onClick={() => void save()}>Сохранить паузу</Button> : <p>Общую паузу меняет royal.</p>}
      <p>Успешные запросы по роботам. День и месяц — по московскому времени. Счётчики накапливаются с запуска встроенного сервиса.</p>
      {usage.length ? <div className="table-scroll"><table><caption>Запросы сотрудников доступных вам парков</caption><thead><tr><th>Сотрудник</th><th>Сегодня</th><th>Месяц</th><th>Всего</th></tr></thead><tbody>{usage.map(row => <tr key={row.user_id}><td>{row.username}</td><td>{row.stats.today}</td><td>{row.stats.month}</td><td>{row.stats.total}</td></tr>)}</tbody></table></div> : <p>Запросов пока нет.</p>}
    </div> : null}
    <Button disabled={busy || loading} onClick={() => void load()}>Обновить статистику и паузу</Button>
  </Panel>
}
