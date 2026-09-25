import type { ScheduleEntry } from '../../api'

const deviceTimeZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
const dayFormatter = (timeZone: string) => new Intl.DateTimeFormat('en-CA', {
  timeZone,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

const dateTimeFormatter = (timeZone: string) => new Intl.DateTimeFormat('en-CA', {
  timeZone,
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

function calendarDate(date: Date, timeZone: string): CalendarDate {
  const value = parts(date, dayFormatter(timeZone))
  return { year: value.year, month: value.month, day: value.day }
}

function zonedMidnight({ year, month, day }: CalendarDate, timeZone: string): Date {
  const target = Date.UTC(year, month - 1, day)
  let instant = target

  for (let attempt = 0; attempt < 2; attempt += 1) {
    const value = parts(new Date(instant), dateTimeFormatter(timeZone)) as CalendarDateTime
    const represented = Date.UTC(value.year, value.month - 1, value.day, value.hour, value.minute, value.second)
    instant += target - represented
  }

  return new Date(instant)
}

function calendarCoordinate({ year, month, day }: CalendarDate): Date {
  return new Date(Date.UTC(year, month - 1, day))
}

function dateFromCoordinate(date: Date, timeZone: string): Date {
  return zonedMidnight({
    year: date.getUTCFullYear(),
    month: date.getUTCMonth() + 1,
    day: date.getUTCDate(),
  }, timeZone)
}

function nextDay(date: Date, timeZone: string): Date {
  const coordinate = calendarCoordinate(calendarDate(date, timeZone))
  coordinate.setUTCDate(coordinate.getUTCDate() + 1)
  return dateFromCoordinate(coordinate, timeZone)
}

function compareScheduleEntries(left: ScheduleEntry, right: ScheduleEntry): number {
  const startDifference = new Date(left.start_at).getTime() - new Date(right.start_at).getTime()
  if (startDifference !== 0) return startDifference
  return left.id < right.id ? -1 : left.id > right.id ? 1 : 0
}

export function formatDayKey(date: Date, timeZone = deviceTimeZone()): string {
  timeZone = typeof timeZone === 'string' ? timeZone : deviceTimeZone()
  const { year, month, day } = calendarDate(date, timeZone)
  return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`
}

export function visibleRange(anchor: Date, view: 'week' | 'month', timeZone = deviceTimeZone()): { start: Date; end: Date; days: Date[] } {
  const anchorCoordinate = calendarCoordinate(calendarDate(anchor, timeZone))
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
    days.push(dateFromCoordinate(cursor, timeZone))
  }

  return {
    start: dateFromCoordinate(startCoordinate, timeZone),
    end: dateFromCoordinate(endCoordinate, timeZone),
    days,
  }
}

export function projectSchedule(items: ScheduleEntry[], days: Date[], timeZone = deviceTimeZone()): Map<number, Map<string, ScheduleEntry[]>> {
  const result = new Map<number, Map<string, ScheduleEntry[]>>()

  for (const item of items) {
    const entryStart = new Date(item.start_at)
    const entryEnd = new Date(item.end_at)

    for (const day of days) {
      const dayStart = zonedMidnight(calendarDate(day, timeZone), timeZone)
      const dayEnd = nextDay(dayStart, timeZone)
      if (entryStart >= dayEnd || entryEnd <= dayStart) continue

      const ownerDays = result.get(item.owner_user_id) ?? new Map<string, ScheduleEntry[]>()
      const key = formatDayKey(dayStart, timeZone)
      ownerDays.set(key, [...(ownerDays.get(key) ?? []), item])
      result.set(item.owner_user_id, ownerDays)
    }
  }

  for (const ownerDays of result.values()) {
    for (const [key, entries] of ownerDays) {
      ownerDays.set(key, [...entries].sort(compareScheduleEntries))
    }
  }

  return result
}
