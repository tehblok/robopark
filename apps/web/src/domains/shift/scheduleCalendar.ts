import type { ScheduleEntry } from '../../api'

const MOSCOW_TIME_ZONE = 'Europe/Moscow'

const dayFormatter = new Intl.DateTimeFormat('en-CA', {
  timeZone: MOSCOW_TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

const dateTimeFormatter = new Intl.DateTimeFormat('en-CA', {
  timeZone: MOSCOW_TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
})

type CalendarDate = { year: number; month: number; day: number }
type CalendarDateTime = CalendarDate & { hour: number; minute: number; second: number }

function parts(date: Date, formatter: Intl.DateTimeFormat): Record<string, number> {
  return Object.fromEntries(
    formatter.formatToParts(date)
      .filter(part => part.type !== 'literal')
      .map(part => [part.type, Number(part.value)]),
  )
}

function calendarDate(date: Date): CalendarDate {
  const value = parts(date, dayFormatter)
  return { year: value.year, month: value.month, day: value.day }
}

function moscowMidnight({ year, month, day }: CalendarDate): Date {
  const target = Date.UTC(year, month - 1, day)
  let instant = target

  for (let attempt = 0; attempt < 2; attempt += 1) {
    const value = parts(new Date(instant), dateTimeFormatter) as CalendarDateTime
    const represented = Date.UTC(value.year, value.month - 1, value.day, value.hour, value.minute, value.second)
    instant += target - represented
  }

  return new Date(instant)
}

function calendarCoordinate({ year, month, day }: CalendarDate): Date {
  return new Date(Date.UTC(year, month - 1, day))
}

function dateFromCoordinate(date: Date): Date {
  return moscowMidnight({
    year: date.getUTCFullYear(),
    month: date.getUTCMonth() + 1,
    day: date.getUTCDate(),
  })
}

function nextDay(date: Date): Date {
  const coordinate = calendarCoordinate(calendarDate(date))
  coordinate.setUTCDate(coordinate.getUTCDate() + 1)
  return dateFromCoordinate(coordinate)
}

export function formatDayKey(date: Date): string {
  const { year, month, day } = calendarDate(date)
  return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`
}

export function visibleRange(anchor: Date, view: 'week' | 'month'): { start: Date; end: Date; days: Date[] } {
  const anchorCoordinate = calendarCoordinate(calendarDate(anchor))
  const startCoordinate = new Date(anchorCoordinate)

  if (view === 'week') {
    startCoordinate.setUTCDate(startCoordinate.getUTCDate() - ((startCoordinate.getUTCDay() + 6) % 7))
  } else {
    startCoordinate.setUTCDate(1)
  }

  const endCoordinate = new Date(startCoordinate)
  if (view === 'week') {
    endCoordinate.setUTCDate(endCoordinate.getUTCDate() + 7)
  } else {
    endCoordinate.setUTCMonth(endCoordinate.getUTCMonth() + 1)
  }

  const days: Date[] = []
  for (const cursor = new Date(startCoordinate); cursor < endCoordinate; cursor.setUTCDate(cursor.getUTCDate() + 1)) {
    days.push(dateFromCoordinate(cursor))
  }

  return {
    start: dateFromCoordinate(startCoordinate),
    end: dateFromCoordinate(endCoordinate),
    days,
  }
}

export function projectSchedule(items: ScheduleEntry[], days: Date[]): Map<number, Map<string, ScheduleEntry[]>> {
  const result = new Map<number, Map<string, ScheduleEntry[]>>()

  for (const item of items) {
    const entryStart = new Date(item.start_at)
    const entryEnd = new Date(item.end_at)

    for (const day of days) {
      const dayStart = moscowMidnight(calendarDate(day))
      const dayEnd = nextDay(dayStart)
      if (entryStart >= dayEnd || entryEnd <= dayStart) continue

      const ownerDays = result.get(item.owner_user_id) ?? new Map<string, ScheduleEntry[]>()
      const key = formatDayKey(dayStart)
      ownerDays.set(key, [...(ownerDays.get(key) ?? []), item])
      result.set(item.owner_user_id, ownerDays)
    }
  }

  return result
}
