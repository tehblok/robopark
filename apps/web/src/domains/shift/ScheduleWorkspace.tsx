import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { api, type ScheduleCreate, type ScheduleEntry, type ScheduleListParams, type ScheduleParticipant, type User } from '../../api'
import { useAuth } from '../../auth-context'
import { useParkScope } from '../../app/park/parkScope'
import { Button } from '../../design-system/actions/Button'
import { EmptyState, ErrorState, LoadingState } from '../../design-system/feedback/AsyncState'
import { PageLayout, Panel } from '../../design-system/layout/PageLayout'
import { NotificationCenter } from '../../pwa/NotificationCenter'
import { visibleRange } from './scheduleCalendar'
import './ScheduleWorkspace.css'

export type ScheduleApiClient = {
  schedules: (params: ScheduleListParams) => Promise<ScheduleEntry[]>
  scheduleCreate: (payload: ScheduleCreate) => Promise<ScheduleEntry>
  scheduleUpdate: (id: string, payload: Pick<ScheduleCreate, 'kind' | 'start_at' | 'end_at'>) => Promise<ScheduleEntry>
  scheduleDelete: (id: string) => Promise<void>
  scheduleBulk?: (payload: ScheduleCreate & { owner_user_ids: number[]; repeat_count: number; repeat_every_days: number }) => Promise<ScheduleEntry[]>
  scheduleParticipants: (parkId: number) => Promise<ScheduleParticipant[]>
}

const kindLabel = { shift: 'Смена', vacation: 'Отпуск', sick: 'Болезнь' } as const
const roleLabel: Record<string, string> = { mechanic: 'Механик', operator: 'Оператор' }
const moscowIso = (value: string) => `${value}:00+03:00`
const moscowParts = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
const toMoscowInput = (value: string) => moscowParts.format(new Date(value)).replace(' ', 'T')
const formatMoscow = (value: string) => new Date(value).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow' })

export function ScheduleWorkspace({ apiClient = api, initialAnchor, user, selectedParkId }: { apiClient?: ScheduleApiClient; initialAnchor?: Date; user: User; selectedParkId?: number | null }) {
  const [scheduleState, setScheduleState] = useState<{ key: string; items: ScheduleEntry[] } | null>(null)
  const scheduleStateRef = useRef(scheduleState)
  const [errorKey, setErrorKey] = useState<string | null>(null)
  const [editor, setEditor] = useState(false)
  const [kind, setKind] = useState<ScheduleCreate['kind']>('shift')
  const [startAt, setStartAt] = useState('')
  const [endAt, setEndAt] = useState('')
  const [participantState, setParticipantState] = useState<{ key: string; items: ScheduleParticipant[] } | null>(null)
  const [employeeIds, setEmployeeIds] = useState<number[]>([])
  const [repeatCount, setRepeatCount] = useState(1)
  const [editing, setEditing] = useState<ScheduleEntry | null>(null)
  const [view, setView] = useState<'week' | 'month'>('week')
  const [anchor] = useState(() => initialAnchor ?? new Date())
  const range = useMemo(() => visibleRange(anchor, view), [anchor, view])
  const scheduleGeneration = useRef(0)
  const employeeGeneration = useRef(0)
  const parkId = selectedParkId
  const ownerUserId = ['admin', 'royal'].includes(user.role) ? undefined : user.id
  const permissionsKey = [...(user.permissions ?? [])].sort().join('\u0000')
  const scopeKey = parkId == null ? null : JSON.stringify([user.id, user.role, permissionsKey, parkId])
  const requestKey = scopeKey == null ? null : JSON.stringify([scopeKey, range.start.toISOString(), range.end.toISOString()])
  const items = requestKey != null && scheduleState?.key === requestKey ? scheduleState.items : null
  const employees = scopeKey != null && participantState?.key === scopeKey ? participantState.items : []
  useEffect(() => {
    const generation = ++scheduleGeneration.current
    if (parkId == null || requestKey == null) return
    setErrorKey(null)
    void apiClient.schedules({ parkId, ownerUserId, startAt: range.start.toISOString(), endAt: range.end.toISOString() }).then(value => {
      if (generation !== scheduleGeneration.current) return
      const next = { key: requestKey, items: value }
      scheduleStateRef.current = next
      setScheduleState(next)
    }).catch(() => {
      if (generation === scheduleGeneration.current && scheduleStateRef.current?.key !== requestKey) setErrorKey(requestKey)
    })
    return () => { scheduleGeneration.current += 1 }
  }, [apiClient, ownerUserId, parkId, range.end, range.start, requestKey])
  useEffect(() => {
    const generation = ++employeeGeneration.current
    if (parkId == null || scopeKey == null) return
    if (!['admin', 'royal'].includes(user.role)) {
      setParticipantState({ key: scopeKey, items: [] })
      return
    }
    void apiClient.scheduleParticipants(parkId).then(value => {
      if (generation !== employeeGeneration.current) return
      setParticipantState({ key: scopeKey, items: value })
    }).catch(() => undefined)
    return () => { employeeGeneration.current += 1 }
  }, [apiClient, parkId, scopeKey, user.role])
  if (parkId == null) return <PageLayout title="График"><EmptyState title="Выберите парк" /></PageLayout>
  if (errorKey === requestKey) return <PageLayout title="График"><ErrorState description="Повторите загрузку позже." title="Не удалось загрузить график" /></PageLayout>
  if (!items) return <PageLayout title="График"><LoadingState label="Загружаем график" variant="page" /></PageLayout>
  const admin = user.role === 'admin'
  const royal = user.role === 'royal'
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!parkId || !startAt || !endAt) return
    const base = { park_id: parkId, kind, start_at: moscowIso(startAt), end_at: moscowIso(endAt), owner_user_id: undefined }
    if (editing) {
      const updated = await apiClient.scheduleUpdate(editing.id, base)
      setScheduleState(current => current?.key === requestKey ? { ...current, items: current.items.map(item => item.id === updated.id ? updated : item) } : current)
    } else if (royal && employeeIds.length && apiClient.scheduleBulk) {
      const created = await apiClient.scheduleBulk({ ...base, owner_user_ids: employeeIds, repeat_count: repeatCount, repeat_every_days: 7 })
      setScheduleState(current => current?.key === requestKey ? { ...current, items: [...current.items, ...created] } : current)
    } else {
      const created = await apiClient.scheduleCreate(base)
      setScheduleState(current => current?.key === requestKey ? { ...current, items: [...current.items, created] } : current)
    }
    setEditor(false); setEditing(null)
  }
  const openCreate = () => { setEditing(null); setKind('shift'); setStartAt(''); setEndAt(''); setEmployeeIds([]); setEditor(true) }
  const openEdit = (item: ScheduleEntry) => { setEditing(item); setKind(item.kind); setStartAt(toMoscowInput(item.start_at)); setEndAt(toMoscowInput(item.end_at)); setEditor(true) }
  const remove = async (id: string) => { await apiClient.scheduleDelete(id); setScheduleState(current => current?.key === requestKey ? { ...current, items: current.items.filter(item => item.id !== id) } : current) }
  return <PageLayout className="rp-schedule" title={admin || royal ? 'График команды' : 'Мой график'} description="Смены, отпуск и болезнь. Время указано по Москве." actions={<div className="rp-schedule__view"><Button onClick={() => setView('week')} size="compact" variant={view === 'week' ? 'primary' : 'secondary'}>Неделя</Button><Button onClick={() => setView('month')} size="compact" variant={view === 'month' ? 'primary' : 'secondary'}>Месяц</Button></div>}>
    <Panel title={admin || royal ? 'График команды' : 'Мои периоды'} actions={!admin ? <Button onClick={openCreate}>Добавить период</Button> : undefined}>
      {items.length === 0 ? <EmptyState title="Периодов пока нет" /> : <ul className="rp-schedule__list" data-view={view}>{items.map(item => { const employee = employees.find(value => value.id === item.owner_user_id); return <li key={item.id} className={item.warnings.includes('overlap') ? 'is-warning' : ''}><strong>{item.owner_user_id === user.id ? `Моя ${kindLabel[item.kind].toLowerCase()}` : `${kindLabel[item.kind]} · ${employee?.display_name ?? 'Сотрудник'}`}</strong><span>{formatMoscow(item.start_at)} — {formatMoscow(item.end_at)}</span>{item.warnings.includes('overlap') ? <em>Пересечение</em> : null}{royal || item.owner_user_id === user.id ? <div><Button onClick={() => openEdit(item)} size="compact" variant="secondary">Изменить</Button><Button onClick={() => void remove(item.id)} size="compact" variant="ghost">Удалить</Button></div> : null}</li> })}</ul>}
    </Panel>
    {editor ? <Panel title={editing ? 'Изменить период' : 'Новый период'}><form className="rp-schedule__editor" onSubmit={submit}>{royal && !editing ? <><fieldset className="rp-schedule__employees"><legend>Сотрудники</legend>{employees.length ? employees.map(employee => <label key={employee.id}><input checked={employeeIds.includes(employee.id)} onChange={event => setEmployeeIds(current => event.target.checked ? [...current, employee.id] : current.filter(id => id !== employee.id))} type="checkbox" />{employee.display_name} · {roleLabel[employee.role] ?? employee.role}</label>) : <span>Нет доступных сотрудников</span>}</fieldset><label>Повторов<input aria-label="Повторов" max="52" min="1" onChange={event => setRepeatCount(Number(event.target.value))} type="number" value={repeatCount} /></label></> : null}<label>Тип<select aria-label="Тип" onChange={event => setKind(event.target.value as ScheduleCreate['kind'])} value={kind}><option value="shift">Смена</option><option value="vacation">Отпуск</option><option value="sick">Болезнь</option></select></label><label>Начало<input aria-label="Начало" onChange={event => setStartAt(event.target.value)} required type="datetime-local" value={startAt} /></label><label>Конец<input aria-label="Конец" onChange={event => setEndAt(event.target.value)} required type="datetime-local" value={endAt} /></label><div><Button disabled={royal && !editing && employeeIds.length === 0} type="submit">Сохранить</Button><Button onClick={() => setEditor(false)} type="button" variant="ghost">Отмена</Button></div></form></Panel> : null}
    <NotificationCenter />
  </PageLayout>
}

export function SchedulePage() {
  const { user } = useAuth()
  const { parkId } = useParkScope()
  return user ? <ScheduleWorkspace selectedParkId={parkId} user={user} /> : null
}
