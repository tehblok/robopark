import { useEffect, useState, type FormEvent } from 'react'
import { api, type AdminUser, type ScheduleCreate, type ScheduleEntry, type User } from '../../api'
import { useAuth } from '../../auth-context'
import { useParkScope } from '../../app/park/parkScope'
import { Button } from '../../design-system/actions/Button'
import { EmptyState, ErrorState, LoadingState } from '../../design-system/feedback/AsyncState'
import { PageLayout, Panel } from '../../design-system/layout/PageLayout'
import { NotificationCenter } from '../../pwa/NotificationCenter'
import './ScheduleWorkspace.css'

export type ScheduleApiClient = {
  schedules: (parkId?: number) => Promise<ScheduleEntry[]>
  scheduleCreate: (payload: ScheduleCreate) => Promise<ScheduleEntry>
  scheduleUpdate: (id: string, payload: Pick<ScheduleCreate, 'kind' | 'start_at' | 'end_at'>) => Promise<ScheduleEntry>
  scheduleDelete: (id: string) => Promise<void>
  scheduleBulk?: (payload: ScheduleCreate & { owner_user_ids: number[]; repeat_count: number; repeat_every_days: number }) => Promise<ScheduleEntry[]>
  adminUsers?: () => Promise<Array<Pick<AdminUser, 'id' | 'username' | 'role' | 'is_active' | 'access_status' | 'parks'>>>
}

const kindLabel = { shift: 'Смена', vacation: 'Отпуск', sick: 'Болезнь' } as const
const roleLabel: Record<string, string> = { mechanic: 'Механик', operator: 'Оператор' }
const moscowIso = (value: string) => `${value}:00+03:00`

export function ScheduleWorkspace({ apiClient = api, user, selectedParkId }: { apiClient?: ScheduleApiClient; user: User; selectedParkId?: number | null }) {
  const [items, setItems] = useState<ScheduleEntry[] | null>(null)
  const [error, setError] = useState(false)
  const [editor, setEditor] = useState(false)
  const [kind, setKind] = useState<ScheduleCreate['kind']>('shift')
  const [startAt, setStartAt] = useState('')
  const [endAt, setEndAt] = useState('')
  const [employees, setEmployees] = useState<Array<Pick<AdminUser, 'id' | 'username' | 'role' | 'is_active' | 'access_status' | 'parks'>>>([])
  const [employeeIds, setEmployeeIds] = useState<number[]>([])
  const [repeatCount, setRepeatCount] = useState(1)
  const [editing, setEditing] = useState<ScheduleEntry | null>(null)
  const [view, setView] = useState<'week' | 'month'>('week')
  const parkId = selectedParkId ?? user?.parks[0]?.id
  useEffect(() => { if (!user) return; let active = true; void apiClient.schedules(parkId).then(value => { if (active) setItems(value) }).catch(() => { if (active) setError(true) }); return () => { active = false } }, [apiClient, parkId, user])
  useEffect(() => {
    if (!['admin', 'royal'].includes(user.role) || !apiClient.adminUsers) return
    let active = true
    void apiClient.adminUsers().then(value => {
      if (!active) return
      setEmployees(value.filter(item => item.is_active && item.access_status === 'approved' && ['mechanic', 'operator'].includes(item.role) && (!parkId || item.parks.some(park => park.id === parkId))))
    }).catch(() => undefined)
    return () => { active = false }
  }, [apiClient, parkId, user.role])
  if (error) return <PageLayout title="График"><ErrorState description="Повторите загрузку позже." title="Не удалось загрузить график" /></PageLayout>
  if (!items) return <PageLayout title="График"><LoadingState label="Загружаем график" variant="page" /></PageLayout>
  const admin = user.role === 'admin'
  const royal = user.role === 'royal'
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!parkId || !startAt || !endAt) return
    const base = { park_id: parkId, kind, start_at: moscowIso(startAt), end_at: moscowIso(endAt), owner_user_id: undefined }
    if (editing) {
      const updated = await apiClient.scheduleUpdate(editing.id, base)
      setItems(current => current?.map(item => item.id === updated.id ? updated : item) ?? [])
    } else if (royal && employeeIds.length && apiClient.scheduleBulk) {
      const created = await apiClient.scheduleBulk({ ...base, owner_user_ids: employeeIds, repeat_count: repeatCount, repeat_every_days: 7 })
      setItems(current => [...(current ?? []), ...created])
    } else {
      const created = await apiClient.scheduleCreate(base)
      setItems(current => [...(current ?? []), created])
    }
    setEditor(false); setEditing(null)
  }
  const openCreate = () => { setEditing(null); setKind('shift'); setStartAt(''); setEndAt(''); setEmployeeIds([]); setEditor(true) }
  const openEdit = (item: ScheduleEntry) => { setEditing(item); setKind(item.kind); setStartAt(item.start_at.slice(0, 16)); setEndAt(item.end_at.slice(0, 16)); setEditor(true) }
  const remove = async (id: string) => { await apiClient.scheduleDelete(id); setItems(current => current?.filter(item => item.id !== id) ?? []) }
  return <PageLayout className="rp-schedule" title={admin || royal ? 'График команды' : 'Мой график'} description="Смены, отпуск и болезнь. Время указано по Москве." actions={<div className="rp-schedule__view"><Button onClick={() => setView('week')} size="compact" variant={view === 'week' ? 'primary' : 'secondary'}>Неделя</Button><Button onClick={() => setView('month')} size="compact" variant={view === 'month' ? 'primary' : 'secondary'}>Месяц</Button></div>}>
    <Panel title={admin || royal ? 'График команды' : 'Мои периоды'} actions={!admin ? <Button onClick={openCreate}>Добавить период</Button> : undefined}>
      {items.length === 0 ? <EmptyState title="Периодов пока нет" /> : <ul className="rp-schedule__list" data-view={view}>{items.map(item => { const employee = employees.find(value => value.id === item.owner_user_id); return <li key={item.id} className={item.warnings.includes('overlap') ? 'is-warning' : ''}><strong>{item.owner_user_id === user.id ? `Моя ${kindLabel[item.kind].toLowerCase()}` : `${kindLabel[item.kind]} · ${employee?.username ?? 'Сотрудник'}`}</strong><span>{new Date(item.start_at).toLocaleString('ru-RU')} — {new Date(item.end_at).toLocaleString('ru-RU')}</span>{item.warnings.includes('overlap') ? <em>Пересечение</em> : null}{royal || item.owner_user_id === user.id ? <div><Button onClick={() => openEdit(item)} size="compact" variant="secondary">Изменить</Button><Button onClick={() => void remove(item.id)} size="compact" variant="ghost">Удалить</Button></div> : null}</li> })}</ul>}
    </Panel>
    {editor ? <Panel title={editing ? 'Изменить период' : 'Новый период'}><form className="rp-schedule__editor" onSubmit={submit}>{royal && !editing ? <><fieldset className="rp-schedule__employees"><legend>Сотрудники</legend>{employees.length ? employees.map(employee => <label key={employee.id}><input checked={employeeIds.includes(employee.id)} onChange={event => setEmployeeIds(current => event.target.checked ? [...current, employee.id] : current.filter(id => id !== employee.id))} type="checkbox" />{employee.username} · {roleLabel[employee.role] ?? employee.role}</label>) : <span>Нет доступных сотрудников</span>}</fieldset><label>Повторов<input aria-label="Повторов" max="52" min="1" onChange={event => setRepeatCount(Number(event.target.value))} type="number" value={repeatCount} /></label></> : null}<label>Тип<select aria-label="Тип" onChange={event => setKind(event.target.value as ScheduleCreate['kind'])} value={kind}><option value="shift">Смена</option><option value="vacation">Отпуск</option><option value="sick">Болезнь</option></select></label><label>Начало<input aria-label="Начало" onChange={event => setStartAt(event.target.value)} required type="datetime-local" value={startAt} /></label><label>Конец<input aria-label="Конец" onChange={event => setEndAt(event.target.value)} required type="datetime-local" value={endAt} /></label><div><Button disabled={royal && !editing && employeeIds.length === 0} type="submit">Сохранить</Button><Button onClick={() => setEditor(false)} type="button" variant="ghost">Отмена</Button></div></form></Panel> : null}
    <NotificationCenter />
  </PageLayout>
}

export function SchedulePage() {
  const { user } = useAuth()
  const { parkId } = useParkScope()
  return user ? <ScheduleWorkspace selectedParkId={parkId} user={user} /> : null
}
