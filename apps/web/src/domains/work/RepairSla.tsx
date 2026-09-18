export type RepairSlaProps = {
  deadline?: string | null
  source?: 'status_history' | 'estimated' | null
  now?: number
}

export function RepairSla({ deadline, now = Date.now() }: RepairSlaProps) {
  const parsed = deadline ? Date.parse(deadline) : Number.NaN
  let text = 'Нет данных о начале очереди'
  if (Number.isFinite(parsed)) {
    const delta = parsed - now
    const hours = delta >= 0
      ? moscowWorkingHoursBetween(now, parsed)
      : moscowWorkingHoursBetween(parsed, now)
    text = delta >= 0
      ? `Осталось ${formatDurationHours(hours)}`
      : `Просрочено на ${formatDurationHours(hours)}`
  }
  return (
    <span aria-live="polite" role="status">
      {text}
    </span>
  )
}
import { formatDurationHours, moscowWorkingHoursBetween } from '../../lib/timeFormat'
