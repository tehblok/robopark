import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { api, type ScheduleCopyCreate, type ScheduleCreate, type ScheduleEntry, type ScheduleListParams, type ScheduleParticipant, type SchedulePatternCreate, type User } from '../../api'
import { useAuth } from '../../auth-context'
import { useParkScope } from '../../app/park/parkScope'
import { Button } from '../../design-system/actions/Button'
import { EmptyState, ErrorState, LoadingState } from '../../design-system/feedback/AsyncState'
import { PageLayout, Panel } from '../../design-system/layout/PageLayout'
import { TabPanel, Tabs } from '../../design-system/navigation/Tabs'
import { NotificationCenter } from '../../pwa/NotificationCenter'
import { visibleRange } from './scheduleCalendar'
import { ScheduleCalendar, ScheduleViewControls, type ScheduleView } from './PersonalScheduleCalendar'
import { SchedulePlanner } from './SchedulePlanner'
import { ScheduleTeamGrid } from './ScheduleTeamGrid'
import './ScheduleWorkspace.css'

export type ScheduleApiClient = {
  schedules: (params: ScheduleListParams) => Promise<ScheduleEntry[]>
  scheduleCreate: (payload: ScheduleCreate) => Promise<ScheduleEntry>
  scheduleUpdate: (id: string, payload: Pick<ScheduleCreate, 'kind' | 'start_at' | 'end_at'>) => Promise<ScheduleEntry>
  scheduleDelete: (id: string) => Promise<void>
  scheduleBulk?: (payload: ScheduleCreate & { owner_user_ids: number[]; repeat_count: number; repeat_every_days: number }) => Promise<ScheduleEntry[]>
  schedulePattern: (payload: SchedulePatternCreate) => Promise<ScheduleEntry[]>
  scheduleCopy: (payload: ScheduleCopyCreate) => Promise<ScheduleEntry[]>
  scheduleParticipants: (parkId: number) => Promise<ScheduleParticipant[]>
}

const moscowIso = (value: string) => `${value}:00+03:00`
const moscowParts = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
const toMoscowInput = (value: string) => moscowParts.format(new Date(value)).replace(' ', 'T')
type ScheduleSection = 'mine' | 'team' | 'planning'

export function ScheduleWorkspace({ apiClient = api, initialAnchor, user, selectedParkId }: { apiClient?: ScheduleApiClient; initialAnchor?: Date; user: User; selectedParkId?: number | null }) {
  const [scheduleState, setScheduleState] = useState<{ key: string; items: ScheduleEntry[] } | null>(null)
  const scheduleStateRef = useRef(scheduleState)
  const [errorKey, setErrorKey] = useState<string | null>(null)
  const [editor, setEditor] = useState(false)
  const [kind, setKind] = useState<ScheduleCreate['kind']>('shift')
  const [startAt, setStartAt] = useState('')
  const [endAt, setEndAt] = useState('')
  const [participantState, setParticipantState] = useState<{ key: string; items: ScheduleParticipant[] } | null>(null)
  const [editing, setEditing] = useState<ScheduleEntry | null>(null)
  const [view, setView] = useState<ScheduleView>('week')
  const [anchor, setAnchor] = useState(() => initialAnchor ?? new Date())
  const [section, setSection] = useState<ScheduleSection>(() => user.role === 'admin' || user.role === 'royal' ? 'team' : 'mine')
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
    const controller = new AbortController()
    setErrorKey(null)
    void apiClient.schedules({ parkId, ownerUserId, startAt: range.start.toISOString(), endAt: range.end.toISOString(), signal: controller.signal }).then(value => {
      if (generation !== scheduleGeneration.current) return
      const next = { key: requestKey, items: value }
      scheduleStateRef.current = next
      setScheduleState(next)
    }).catch(() => {
      if (generation === scheduleGeneration.current && scheduleStateRef.current?.key !== requestKey) setErrorKey(requestKey)
    })
    return () => { controller.abort(); scheduleGeneration.current += 1 }
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
  const tabs = admin
    ? [{ id: 'team', label: 'Команда' }]
    : royal
      ? [{ id: 'mine', label: 'Мой календарь' }, { id: 'team', label: 'Команда' }, { id: 'planning', label: 'Планирование' }]
      : [{ id: 'mine', label: 'Мой календарь' }]
  const selectedSection = tabs.some(tab => tab.id === section) ? section : tabs[0].id as ScheduleSection
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!parkId || !startAt || !endAt) return
    const base = { park_id: parkId, kind, start_at: moscowIso(startAt), end_at: moscowIso(endAt), owner_user_id: undefined }
    if (editing) {
      const updated = await apiClient.scheduleUpdate(editing.id, base)
      setScheduleState(current => current?.key === requestKey ? { ...current, items: current.items.map(item => item.id === updated.id ? updated : item) } : current)
    } else {
      const created = await apiClient.scheduleCreate(base)
      setScheduleState(current => current?.key === requestKey ? { ...current, items: [...current.items, created] } : current)
    }
    setEditor(false); setEditing(null)
  }
  const openCreate = () => { setEditing(null); setKind('shift'); setStartAt(''); setEndAt(''); setEditor(true) }
  const openEdit = (item: ScheduleEntry) => { setEditing(item); setKind(item.kind); setStartAt(toMoscowInput(item.start_at)); setEndAt(toMoscowInput(item.end_at)); setEditor(true) }
  const remove = async (id: string) => { await apiClient.scheduleDelete(id); setScheduleState(current => current?.key === requestKey ? { ...current, items: current.items.filter(item => item.id !== id) } : current) }
  const editorPanel = editor ? <Panel title={editing ? 'Изменить период' : 'Новый период'}><form className="rp-schedule__editor" onSubmit={submit}><label>Тип<select aria-label="Тип" onChange={event => setKind(event.target.value as ScheduleCreate['kind'])} value={kind}><option value="shift">Смена</option><option value="vacation">Отпуск</option><option value="sick">Болезнь</option></select></label><label>Начало<input aria-label="Начало" onChange={event => setStartAt(event.target.value)} required type="datetime-local" value={startAt} /></label><label>Конец<input aria-label="Конец" onChange={event => setEndAt(event.target.value)} required type="datetime-local" value={endAt} /></label><div><Button type="submit">Сохранить</Button><Button onClick={() => setEditor(false)} type="button" variant="ghost">Отмена</Button></div></form></Panel> : null
  const addPlanned = (created: ScheduleEntry[]) => setScheduleState(current => current?.key === requestKey ? { ...current, items: [...current.items, ...created] } : current)
  const moveRange = (direction: -1 | 1) => setAnchor(direction < 0 ? new Date(range.start.getTime() - 12 * 60 * 60 * 1000) : range.end)
  return <PageLayout className="rp-schedule" title={admin || royal ? 'График команды' : 'Мой график'} description="Смены, отпуск и болезнь. Время указано по Москве." actions={<div className="rp-schedule__range"><Button aria-label="Предыдущий период" onClick={() => moveRange(-1)} size="compact" variant="secondary">Назад</Button><Button onClick={() => setAnchor(new Date())} size="compact" variant="ghost">Сегодня</Button><Button aria-label="Следующий период" onClick={() => moveRange(1)} size="compact" variant="secondary">Вперёд</Button></div>}>
    <Tabs ariaLabel="Разделы графика" items={tabs} onChange={id => { setEditor(false); setSection(id as ScheduleSection) }} panelIdFor={id => `schedule-panel-${id}`} value={selectedSection} />
    {!admin ? <TabPanel active={selectedSection === 'mine'} id="schedule-panel-mine" labelledBy="tab-mine">
      {selectedSection === 'mine' ? <><Panel actions={<Button onClick={openCreate}>Добавить период</Button>} title="Мой календарь">
        <ScheduleCalendar days={range.days} items={items} onDelete={item => void remove(item.id)} onEdit={openEdit} onViewChange={setView} ownerUserId={user.id} selectedDate={anchor} view={view} />
      </Panel>{editorPanel}</> : null}
    </TabPanel> : null}
    {admin || royal ? <TabPanel active={selectedSection === 'team'} id="schedule-panel-team" labelledBy="tab-team">
      {selectedSection === 'team' ? <><Panel actions={<ScheduleViewControls onViewChange={setView} view={view} />} title="Команда">
        <ScheduleTeamGrid days={range.days} employees={employees} items={items} onDelete={royal ? item => void remove(item.id) : undefined} onEdit={royal ? openEdit : undefined} selectedDate={anchor} />
      </Panel>{editorPanel}</> : null}
    </TabPanel> : null}
    {royal ? <TabPanel active={selectedSection === 'planning'} id="schedule-panel-planning" labelledBy="tab-planning">
      {selectedSection === 'planning' ? <Panel description="Создайте смены по шаблону или скопируйте существующий период." title="Планирование"><SchedulePlanner apiClient={apiClient} employees={employees} onCreated={addPlanned} parkId={parkId} /></Panel> : null}
    </TabPanel> : null}
    <NotificationCenter />
  </PageLayout>
}

export function SchedulePage() {
  const { user } = useAuth()
  const { parkId } = useParkScope()
  return user ? <ScheduleWorkspace selectedParkId={parkId} user={user} /> : null
}
