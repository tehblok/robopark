const STATUS_LABELS: Record<string, string> = {
  new: 'Новая',
  open: 'Открыта',
  queued: 'В очереди',
  diagnostics: 'Диагностика',
  in_progress: 'В работе',
  inprogress: 'В работе',
  ready_for_test: 'На проверке',
  testing: 'На проверке',
  closed: 'Закрыта',
  done: 'Закрыта',
  cancelled: 'Отменена',
}

function normalize(value: string) {
  return value.trim().toLowerCase().replace(/[\s-]+/g, '_')
}

export function trackerStatusLabel(status: string, statusKey?: string | null) {
  return STATUS_LABELS[normalize(statusKey || status)] ?? status
}
