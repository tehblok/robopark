import { reportKindLabel, reportStatusLabel } from '../../i18n/ru'

export function formatReportDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('ru-RU', { timeZone: 'Europe/Moscow' })
}

export function trackerHref(key: string | null, url: string | null): string | null {
  if (url) return url
  if (key) return `https://tracker.yandex.ru/${key}`
  return null
}

export function statusBadgeClass(status: string): string {
  switch (status) {
    case 'open':
      return 'badge badge-warn'
    case 'returned':
      return 'badge badge-danger'
    case 'done':
      return 'badge badge-ok'
    default:
      return 'badge badge-muted'
  }
}

export function reportKindText(kind: string): string {
  return reportKindLabel(kind)
}

export function reportStatusText(status: string): string {
  return reportStatusLabel(status)
}
