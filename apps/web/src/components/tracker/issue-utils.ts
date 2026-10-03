import type { TrackerIssue, TrackerPerson } from '../../api'
import { ru } from '../../i18n/ru'
import { formatDurationHours } from '../../lib/timeFormat'

/** Status buckets used by the tasks filter bar. */
export const TASK_FILTERS = [
  'all',
  'moving',
  'queued',
  'waiting_team',
  'waiting_parts',
  'other',
] as const

/** Tracker sends `+0000` offsets, which Safari/Firefox refuse to parse. */
export function parseTrackerDate(raw?: string | null): Date | null {
  if (!raw) return null
  let text = raw.trim().replace('Z', '+00:00')
  // "+0000" -> "+00:00"
  const tail = text.slice(-5)
  if (/^[+-]\d{4}$/.test(tail)) {
    text = `${text.slice(0, -5)}${tail.slice(0, 3)}:${tail.slice(3)}`
  }
  const parsed = new Date(text)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

export function formatDateTime(raw?: string | null): string {
  const date = parseTrackerDate(raw)
  if (!date) return ru.tracker.fields.empty
  return date.toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** Task age is a duration, shown with the same precision everywhere. */
export function formatAge(hours?: string | null): string {
  if (!hours) return ''
  const value = Number(hours)
  if (!Number.isFinite(value) || value < 0) return ''
  return formatDurationHours(value)
}

export function formatFileSize(bytes?: number | null): string {
  if (bytes == null || bytes <= 0) return ''
  const units = ['Б', 'КБ', 'МБ', 'ГБ']
  let size = bytes
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit += 1
  }
  return `${size < 10 && unit > 0 ? size.toFixed(1) : Math.round(size)} ${units[unit]}`
}

export function isStale(hours?: string | null, threshold = 24): boolean {
  const value = Number(hours)
  return Number.isFinite(value) && value >= threshold
}

export function personName(person?: TrackerPerson | null): string {
  const name = person?.display?.trim() || person?.login?.trim()
  return name || ru.tracker.fields.nobody
}

export function initials(person?: TrackerPerson | null): string {
  const name = person?.display?.trim() || person?.login?.trim() || ''
  if (!name) return '—'
  const parts = name.split(/\s+/).filter(Boolean)
  if (parts.length >= 2) {
    return `${parts[0][0]}${parts[1][0]}`.toUpperCase()
  }
  return name.slice(0, 2).toUpperCase()
}

export function priorityLabel(priority?: string | null): string {
  const raw = (priority || '').trim()
  if (!raw) return ''
  return ru.tracker.priorities[raw.toLowerCase()] ?? raw
}

/** Coarse priority bucket driving the colour of the badge. */
export function priorityTone(priority?: string | null): 'blocker' | 'high' | 'normal' | 'low' {
  const raw = (priority || '').trim().toLowerCase()
  if (!raw) return 'normal'
  if (raw.includes('blocker') || raw.includes('блокер')) return 'blocker'
  if (raw.includes('critical') || raw.includes('критич')) return 'blocker'
  if (raw.includes('major') || raw.includes('важн')) return 'high'
  if (raw.includes('minor') || raw.includes('trivial') || raw.includes('незнач')) return 'low'
  return 'normal'
}

/** Coarse status bucket driving the colour of the status pill. */
export function statusTone(
  issue: Pick<TrackerIssue, 'status' | 'status_key'>,
): 'open' | 'progress' | 'waiting' | 'done' {
  const blob = `${issue.status_key ?? ''} ${issue.status ?? ''}`
    .toLowerCase()
    .replace(/ё/g, 'е')
  if (/closed|закрыт|resolved|решен|cancel|отмен/.test(blob)) return 'done'
  if (/wait|ожидан|ждем|смежник|поставк/.test(blob)) return 'waiting'
  if (/progress|работе|moving|перемещ|transit/.test(blob)) return 'progress'
  return 'open'
}

const ISSUE_TYPE_LABELS: Record<string, string> = {
  task: 'Задача',
  bug: 'Ошибка',
  subtask: 'Подзадача',
  story: 'История',
  epic: 'Эпик',
}

export function issueTypeLabel(type?: string | null): string {
  const value = (type || '').trim()
  return ISSUE_TYPE_LABELS[value.toLowerCase()] ?? value
}
