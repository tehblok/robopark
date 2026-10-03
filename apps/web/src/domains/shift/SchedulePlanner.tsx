import { useRef, useState, type FormEvent } from 'react'
import type { ScheduleCopyCreate, ScheduleEntry, ScheduleParticipant, SchedulePattern, SchedulePatternCreate } from '../../api'
import { Button } from '../../design-system/actions/Button'

type PlannerApiClient = {
  schedulePattern: (payload: SchedulePatternCreate) => Promise<ScheduleEntry[]>
  scheduleCopy: (payload: ScheduleCopyCreate) => Promise<ScheduleEntry[]>
}
type PlannerParticipant = Omit<ScheduleParticipant, 'role'> & { role: ScheduleParticipant['role'] | 'driver' }

const roleLabel: Record<PlannerParticipant['role'], string> = {
  mechanic: 'Механик',
  operator: 'Оператор',
  driver: 'Водитель',
}
const localIso = (value: string) => new Date(value).toISOString()

export function SchedulePlanner({
  apiClient,
  employees,
  onCreated,
  parkId,
  lockEmployees = false,
  onQueue,
  queueReady = true,
}: {
  apiClient: PlannerApiClient
  employees: PlannerParticipant[]
  onCreated?: (entries: ScheduleEntry[]) => void
  parkId: number
  lockEmployees?: boolean
  onQueue?: (action: 'schedule_pattern' | 'schedule_copy', payload: Record<string, unknown>, key: string) => Promise<void>
  queueReady?: boolean
}) {
  const [ownerIds, setOwnerIds] = useState<number[]>(() => lockEmployees ? employees.map(employee => employee.id) : [])
  const [kind, setKind] = useState<ScheduleEntry['kind']>('shift')
  const [pattern, setPattern] = useState<SchedulePattern>('none')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [startTime, setStartTime] = useState('')
  const [endTime, setEndTime] = useState('')
  const [sourceStart, setSourceStart] = useState('')
  const [sourceEnd, setSourceEnd] = useState('')
  const [targetStart, setTargetStart] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const patternAttempt = useRef<{ fingerprint: string; key: string } | null>(null)
  const copyAttempt = useRef<{ fingerprint: string; key: string } | null>(null)
  const availableOwnerIds = new Set(employees.map(employee => employee.id))
  const selectedOwnerIds = lockEmployees
    ? employees.map(employee => employee.id)
    : ownerIds.filter(id => availableOwnerIds.has(id))

  const toggleOwner = (ownerId: number, checked: boolean) => {
    setOwnerIds(current => checked ? [...current, ownerId] : current.filter(id => id !== ownerId))
  }
  const run = async (request: () => Promise<ScheduleEntry[]>, onSuccess?: () => void) => {
    setBusy(true)
    setError(null)
    try {
      const entries = await request()
      if (entries.length) onCreated?.(entries)
      onSuccess?.()
    } catch (reason) {
      setError(reason instanceof Error && reason.message === 'schedule_already_pending'
        ? 'Такой шаблон или копия уже сохранена на устройстве.'
        : 'Не удалось сохранить график. Повторите попытку.')
    } finally {
      setBusy(false)
    }
  }
  const submitPattern = (event: FormEvent) => {
    event.preventDefault()
    if (!selectedOwnerIds.length) return
    const body = {
      park_id: parkId,
      owner_user_ids: selectedOwnerIds,
      kind,
      pattern,
      start_date: startDate,
      end_date: endDate,
      start_time: startTime,
      end_time: endTime,
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
    }
    const fingerprint = JSON.stringify(body)
    const key = patternAttempt.current?.fingerprint === fingerprint
      ? patternAttempt.current.key
      : globalThis.crypto.randomUUID()
    patternAttempt.current = { fingerprint, key }
    void run(() => onQueue
      ? onQueue('schedule_pattern', body, key).then(() => [])
      : apiClient.schedulePattern({ ...body, idempotency_key: key }), () => {
      patternAttempt.current = null
    })
  }
  const submitCopy = (event: FormEvent) => {
    event.preventDefault()
    if (!selectedOwnerIds.length) return
    const body = {
      park_id: parkId,
      owner_user_ids: selectedOwnerIds,
      source_start: localIso(sourceStart),
      source_end: localIso(sourceEnd),
      target_start: localIso(targetStart),
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
    }
    const fingerprint = JSON.stringify(body)
    const key = copyAttempt.current?.fingerprint === fingerprint
      ? copyAttempt.current.key
      : globalThis.crypto.randomUUID()
    copyAttempt.current = { fingerprint, key }
    void run(() => onQueue
      ? onQueue('schedule_copy', body, key).then(() => [])
      : apiClient.scheduleCopy({ ...body, idempotency_key: key }), () => {
      copyAttempt.current = null
    })
  }

  return <div className="rp-schedule-planner">
    <fieldset className="rp-schedule__employees">
      <legend>Сотрудники</legend>
      {employees.length ? employees.map(employee => <label key={employee.id}>
        <input checked={selectedOwnerIds.includes(employee.id)} disabled={lockEmployees || !selectedOwnerIds.includes(employee.id) && selectedOwnerIds.length >= 50} onChange={event => toggleOwner(employee.id, event.target.checked)} type="checkbox" />
        {employee.display_name} · {roleLabel[employee.role]}
      </label>) : <span>Нет доступных сотрудников</span>}
    </fieldset>

    <form className="rp-schedule__editor" onSubmit={submitPattern}>
      <h3>Шаблон смен</h3>
      <label>Тип<select aria-label="Тип" onChange={event => setKind(event.target.value as ScheduleEntry['kind'])} value={kind}><option value="shift">Смена</option><option value="vacation">Отпуск</option><option value="sick">Болезнь</option></select></label>
      <label>Тип графика<select aria-label="Тип графика" onChange={event => setPattern(event.target.value as SchedulePattern)} value={pattern}><option value="none">Одна дата</option><option value="5/2">5/2</option><option value="4/4">4/4</option><option value="3/3">3/3</option><option value="2/2">2/2</option></select></label>
      <label>Первый день смены<input aria-label="Первый день смены" onChange={event => setStartDate(event.target.value)} required type="date" value={startDate} /></label>
      <label>Создавать до<input aria-label="Создавать до" min={startDate || undefined} onChange={event => setEndDate(event.target.value)} required type="date" value={endDate} /></label>
      <label>Время начала<input aria-label="Время начала" onChange={event => setStartTime(event.target.value)} required type="time" value={startTime} /></label>
      <label>Время окончания<input aria-label="Время окончания" onChange={event => setEndTime(event.target.value)} required type="time" value={endTime} /></label>
      <Button disabled={busy || selectedOwnerIds.length === 0 || !queueReady} type="submit">Создать смены</Button>
    </form>

    <form className="rp-schedule__editor" onSubmit={submitCopy}>
      <h3>Копирование периода</h3>
      <label>Копировать с<input aria-label="Копировать с" onChange={event => setSourceStart(event.target.value)} required type="datetime-local" value={sourceStart} /></label>
      <label>Копировать по<input aria-label="Копировать по" onChange={event => setSourceEnd(event.target.value)} required type="datetime-local" value={sourceEnd} /></label>
      <label>Начало копии<input aria-label="Начало копии" onChange={event => setTargetStart(event.target.value)} required type="datetime-local" value={targetStart} /></label>
      <Button disabled={busy || selectedOwnerIds.length === 0 || !queueReady} type="submit" variant="secondary">Копировать период</Button>
    </form>
    {error ? <p role="alert">{error}</p> : null}
  </div>
}
