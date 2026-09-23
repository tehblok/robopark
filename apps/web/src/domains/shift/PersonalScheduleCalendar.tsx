import { useEffect, useMemo, useState } from 'react'
import type { ScheduleEntry } from '../../api'
import { Button } from '../../design-system/actions/Button'
import { formatDayKey, projectSchedule } from './scheduleCalendar'

const personalKindLabel = { shift: 'Моя смена', vacation: 'Мой отпуск', sick: 'Моя болезнь' } as const
const dayLabel = new Intl.DateTimeFormat('ru-RU', { timeZone: 'Europe/Moscow', day: 'numeric', month: 'long' })
const weekdayLabel = new Intl.DateTimeFormat('ru-RU', { timeZone: 'Europe/Moscow', weekday: 'short' })
const periodLabel = new Intl.DateTimeFormat('ru-RU', {
  timeZone: 'Europe/Moscow',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
})

export type ScheduleView = 'week' | 'month'

export function ScheduleViewControls({ view, onViewChange }: { view: ScheduleView; onViewChange: (view: ScheduleView) => void }) {
  return <div className="rp-schedule__view" aria-label="Масштаб календаря">
    <Button aria-pressed={view === 'week'} onClick={() => onViewChange('week')} size="compact" variant={view === 'week' ? 'primary' : 'secondary'}>Неделя</Button>
    <Button aria-pressed={view === 'month'} onClick={() => onViewChange('month')} size="compact" variant={view === 'month' ? 'primary' : 'secondary'}>Месяц</Button>
  </div>
}

function EntryCard({ item, onEdit, onDelete }: { item: ScheduleEntry; onEdit?: (item: ScheduleEntry) => void; onDelete?: (item: ScheduleEntry) => void }) {
  return <article className={`rp-schedule-entry${item.warnings.includes('overlap') ? ' is-warning' : ''}`}>
    <strong>{personalKindLabel[item.kind]}</strong>
    <span>{periodLabel.format(new Date(item.start_at))} — {periodLabel.format(new Date(item.end_at))}</span>
    {item.warnings.includes('overlap') ? <em>Пересечение</em> : null}
    {onEdit || onDelete ? <div className="rp-schedule-entry__actions">
      {onEdit ? <Button onClick={() => onEdit(item)} size="compact" variant="secondary">Изменить</Button> : null}
      {onDelete ? <Button onClick={() => onDelete(item)} size="compact" variant="ghost">Удалить</Button> : null}
    </div> : null}
  </article>
}

export function ScheduleCalendar({
  days,
  items,
  ownerUserId,
  selectedDate,
  view,
  onViewChange,
  onEdit,
  onDelete,
}: {
  days: Date[]
  items: ScheduleEntry[]
  ownerUserId: number
  selectedDate: Date
  view: ScheduleView
  onViewChange: (view: ScheduleView) => void
  onEdit?: (item: ScheduleEntry) => void
  onDelete?: (item: ScheduleEntry) => void
}) {
  const projected = useMemo(() => projectSchedule(items.filter(item => item.owner_user_id === ownerUserId), days), [days, items, ownerUserId])
  const fallbackKey = formatDayKey(days[0] ?? selectedDate)
  const preferredKey = formatDayKey(selectedDate)
  const [selectedKey, setSelectedKey] = useState(() => days.some(day => formatDayKey(day) === preferredKey) ? preferredKey : fallbackKey)

  useEffect(() => {
    if (!days.some(day => formatDayKey(day) === selectedKey)) setSelectedKey(days.some(day => formatDayKey(day) === preferredKey) ? preferredKey : fallbackKey)
  }, [days, fallbackKey, preferredKey, selectedKey])

  const selectedItems = projected.get(ownerUserId)?.get(selectedKey) ?? []

  return <div className="rp-schedule-calendar">
    <ScheduleViewControls onViewChange={onViewChange} view={view} />
    <div className={`rp-schedule-calendar__days rp-schedule-calendar__days--${view}`} aria-label="Дни календаря">
      {days.map(day => {
        const key = formatDayKey(day)
        const count = projected.get(ownerUserId)?.get(key)?.length ?? 0
        return <button aria-label={dayLabel.format(day)} aria-pressed={key === selectedKey} className="rp-schedule-calendar__day" key={key} onClick={() => setSelectedKey(key)} type="button">
          <span>{weekdayLabel.format(day)}</span>
          <strong>{dayLabel.format(day)}</strong>
          {count ? <small>{count}</small> : null}
        </button>
      })}
    </div>
    <section aria-label={`Периоды на ${selectedKey}`} className="rp-schedule-day-cards" data-testid="schedule-day-cards">
      {selectedItems.length ? selectedItems.map(item => <EntryCard item={item} key={item.id} onDelete={onDelete} onEdit={onEdit} />) : <p>На выбранный день периодов нет</p>}
    </section>
  </div>
}
