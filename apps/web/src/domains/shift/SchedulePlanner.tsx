import { useRef, useState, type FormEvent } from 'react'
import type { ScheduleCopyCreate, ScheduleEntry, ScheduleParticipant, SchedulePattern, SchedulePatternCreate } from '../../api'
import { Button } from '../../design-system/actions/Button'

type PlannerApiClient = {
  schedulePattern: (payload: SchedulePatternCreate) => Promise<ScheduleEntry[]>
  scheduleCopy: (payload: ScheduleCopyCreate) => Promise<ScheduleEntry[]>
}

const roleLabel: Record<ScheduleParticipant['role'], string> = {
  mechanic: 'Механик',
  operator: 'Оператор',
}
const localIso = (value: string) => new Date(value).toISOString()

export function SchedulePlanner({
  apiClient,
  employees,
  onCreated,
  parkId,
  lockEmployees = false,
}: {
  apiClient: PlannerApiClient
  employees: ScheduleParticipant[]
  onCreated?: (entries: ScheduleEntry[]) => void
  parkId: number
  lockEmployees?: boolean
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
  const [error, setError] = useState(false)
  const patternAttempt = useRef<{ fingerprint: string; key: string } | null>(null)
  const copyAttempt = useRef<{ fingerprint: string; key: string } | null>(null)

  const toggleOwner = (ownerId: number, checked: boolean) => {
    setOwnerIds(current => checked ? [...current, ownerId] : current.filter(id => id !== ownerId))
  }
  const run = async (request: () => Promise<ScheduleEntry[]>, onSuccess?: () => void) => {
    setBusy(true)
    setError(false)
    try {
      const entries = await request()
      onCreated?.(entries)
      onSuccess?.()
    } catch {
      setError(true)
    } finally {
      setBusy(false)
    }
  }
  const submitPattern = (event: FormEvent) => {
    event.preventDefault()
    if (!ownerIds.length) return
    const body = {
      park_id: parkId,
      owner_user_ids: ownerIds,
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
    void run(() => apiClient.schedulePattern({ ...body, idempotency_key: key }), () => {
      patternAttempt.current = null
    })
  }
  const submitCopy = (event: FormEvent) => {
    event.preventDefault()
    if (!ownerIds.length) return
    const body = {
      park_id: parkId,
      owner_user_ids: ownerIds,
      source_start: localIso(sourceStart),
      source_end: localIso(sourceEnd),
      target_start: localIso(targetStart),
    }
    const fingerprint = JSON.stringify(body)
    const key = copyAttempt.current?.fingerprint === fingerprint
      ? copyAttempt.current.key
      : globalThis.crypto.randomUUID()
    copyAttempt.current = { fingerprint, key }
    void run(() => apiClient.scheduleCopy({ ...body, idempotency_key: key }), () => {
      copyAttempt.current = null
    })
  }

  return <div className="rp-schedule-planner">
    <fieldset className="rp-schedule__employees">
      <legend>Сотрудники</legend>
      {employees.length ? employees.map(employee => <label key={employee.id}>
        <input checked={ownerIds.includes(employee.id)} disabled={lockEmployees || !ownerIds.includes(employee.id) && ownerIds.length >= 50} onChange={event => toggleOwner(employee.id, event.target.checked)} type="checkbox" />
        {employee.display_name} · {roleLabel[employee.role]}
      </label>) : <span>Нет доступных сотрудников</span>}
    </fieldset>

    <form className="rp-schedule__editor" onSubmit={submitPattern}>
      <h3>Шаблон смен</h3>
      <label>Тип<select aria-label="Тип" onChange={event => setKind(event.target.value as ScheduleEntry['kind'])} value={kind}><option value="shift">Смена</option><option value="vacation">Отпуск</option><option value="sick">Болезнь</option></select></label>
      <label>Шаблон<select aria-label="Шаблон" onChange={event => setPattern(event.target.value as SchedulePattern)} value={pattern}><option value="none">Одна дата</option><option value="5/2">5/2</option><option value="2/2">2/2</option><option value="4/4">4/4</option></select></label>
      <label>Дата начала<input aria-label="Дата начала" onChange={event => setStartDate(event.target.value)} required type="date" value={startDate} /></label>
      <label>Дата окончания<input aria-label="Дата окончания" onChange={event => setEndDate(event.target.value)} required type="date" value={endDate} /></label>
      <label>Время начала<input aria-label="Время начала" onChange={event => setStartTime(event.target.value)} required type="time" value={startTime} /></label>
      <label>Время окончания<input aria-label="Время окончания" onChange={event => setEndTime(event.target.value)} required type="time" value={endTime} /></label>
      <Button disabled={busy || ownerIds.length === 0} type="submit">Создать смены</Button>
    </form>

    <form className="rp-schedule__editor" onSubmit={submitCopy}>
      <h3>Копирование периода</h3>
      <label>Копировать с<input aria-label="Копировать с" onChange={event => setSourceStart(event.target.value)} required type="datetime-local" value={sourceStart} /></label>
      <label>Копировать по<input aria-label="Копировать по" onChange={event => setSourceEnd(event.target.value)} required type="datetime-local" value={sourceEnd} /></label>
      <label>Начало копии<input aria-label="Начало копии" onChange={event => setTargetStart(event.target.value)} required type="datetime-local" value={targetStart} /></label>
      <Button disabled={busy || ownerIds.length === 0} type="submit" variant="secondary">Копировать период</Button>
    </form>
    {error ? <p role="alert">Не удалось сохранить график. Повторите попытку.</p> : null}
  </div>
}
