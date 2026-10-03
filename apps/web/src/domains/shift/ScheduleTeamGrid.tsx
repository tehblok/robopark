import { useEffect, useMemo, useState, type CSSProperties } from 'react'
import type { ScheduleEntry, ScheduleParticipant } from '../../api'
import { Button } from '../../design-system/actions/Button'
import { formatDayKey, projectSchedule } from './scheduleCalendar'

const kindLabel = { shift: 'Смена', vacation: 'Отпуск', sick: 'Болезнь' } as const
const roleLabel: Record<string, string> = { mechanic: 'Механик', operator: 'Оператор' }
const dayLabel = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long' })
const compactDayLabel = new Intl.DateTimeFormat('ru-RU', { weekday: 'short', day: '2-digit', month: '2-digit' })
const timeLabel = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' })

function Entry({ item, onEdit, onDelete, pending }: { item: ScheduleEntry; onEdit?: (item: ScheduleEntry) => void; onDelete?: (item: ScheduleEntry) => void; pending?: boolean }) {
  return <article className={`rp-schedule-entry${item.warnings.includes('overlap') ? ' is-warning' : ''}`}>
    <strong>{kindLabel[item.kind]}</strong>
    <span>{timeLabel.format(new Date(item.start_at))} — {timeLabel.format(new Date(item.end_at))}</span>
    {item.warnings.includes('overlap') ? <em>Пересечение</em> : null}
    {pending ? <span className="rp-schedule-entry__pending">Ожидает синхронизации</span> : null}
    {!pending && (onEdit || onDelete) ? <div className="rp-schedule-entry__actions">
      {onEdit ? <Button onClick={() => onEdit(item)} size="compact" variant="secondary">Изменить</Button> : null}
      {onDelete ? <Button onClick={() => onDelete(item)} size="compact" variant="ghost">Удалить</Button> : null}
    </div> : null}
  </article>
}

export function ScheduleTeamGrid({
  days,
  employees,
  items,
  selectedDate,
  onEdit,
  onDelete,
  pendingIds,
}: {
  days: Date[]
  employees: ScheduleParticipant[]
  items: ScheduleEntry[]
  selectedDate: Date
  onEdit?: (item: ScheduleEntry) => void
  onDelete?: (item: ScheduleEntry) => void
  pendingIds?: Set<string>
}) {
  const projected = useMemo(() => projectSchedule(items, days), [days, items])
  const rows = useMemo(
    () => [...employees]
      .filter(employee => employee.role === 'mechanic' || employee.role === 'operator')
      .sort((left, right) => left.display_name.localeCompare(right.display_name, 'ru')),
    [employees],
  )
  const fallbackKey = formatDayKey(days[0] ?? selectedDate)
  const preferredKey = formatDayKey(selectedDate)
  const [selectedKey, setSelectedKey] = useState(() => days.some(day => formatDayKey(day) === preferredKey) ? preferredKey : fallbackKey)

  useEffect(() => {
    if (!days.some(day => formatDayKey(day) === selectedKey)) setSelectedKey(fallbackKey)
  }, [days, fallbackKey, selectedKey])

  const selectedRows = rows.map(employee => ({ employee, entries: projected.get(employee.id)?.get(selectedKey) ?? [] }))

  return <div className="rp-schedule-team">
    <div aria-label="Таблица графика команды" className="rp-schedule-team__matrix-scroll" role="region" tabIndex={0}>
      <div className="rp-schedule-team__matrix" data-testid="schedule-team-grid" role="table" style={{ '--day-count': days.length } as CSSProperties}>
        <div className="rp-schedule-team__header-group" role="rowgroup">
          <div className="rp-schedule-team__header-row" role="row">
            <div className="rp-schedule-team__corner" role="columnheader">Сотрудник</div>
            {days.map(day => <div aria-label={dayLabel.format(day)} className="rp-schedule-team__date" key={formatDayKey(day)} role="columnheader">{compactDayLabel.format(day)}</div>)}
          </div>
        </div>
        <div className="rp-schedule-team__body" role="rowgroup">
          {rows.map(employee => <div className="rp-schedule-team__row" key={employee.id} role="row">
            <div aria-label={`${employee.display_name} · ${roleLabel[employee.role] ?? employee.role}`} className="rp-schedule-team__employee" role="rowheader"><strong>{employee.display_name}</strong><span>{roleLabel[employee.role] ?? employee.role}</span></div>
            {days.map(day => <div className="rp-schedule-team__cell" key={formatDayKey(day)} role="cell">{(projected.get(employee.id)?.get(formatDayKey(day)) ?? []).map(item => <Entry item={item} key={item.id} onDelete={onDelete} onEdit={onEdit} pending={pendingIds?.has(item.id)} />)}</div>)}
          </div>)}
        </div>
      </div>
    </div>
    <div className="rp-schedule-team__mobile">
      <div className="rp-schedule-calendar__days" aria-label="Дни команды">
        {days.map(day => {
          const key = formatDayKey(day)
          return <button aria-label={dayLabel.format(day)} aria-pressed={key === selectedKey} className="rp-schedule-calendar__day" key={key} onClick={() => setSelectedKey(key)} type="button">{compactDayLabel.format(day)}</button>
        })}
      </div>
      <span className="rp-schedule-calendar__scroll-hint">Листайте дни →</span>
      <section className="rp-schedule-day-cards" data-testid="schedule-day-cards">
        {selectedRows.length ? selectedRows.map(({ employee, entries }) => <article className="rp-schedule-employee-card" key={employee.id}>
          <h3>{employee.display_name}</h3>
          <span>{roleLabel[employee.role] ?? employee.role}</span>
          <div>{entries.length ? entries.map(item => <Entry item={item} key={item.id} onDelete={onDelete} onEdit={onEdit} pending={pendingIds?.has(item.id)} />) : <p>Периодов нет</p>}</div>
        </article>) : <p>Участников команды пока нет</p>}
      </section>
    </div>
  </div>
}
