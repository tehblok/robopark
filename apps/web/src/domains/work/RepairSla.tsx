import { workingHoursBetween } from '../../lib/timeFormat'
import { formatSlaRemaining } from './repairSlaFormat'

export type RepairSlaProps = {
  deadline?: string | null
  queuedAt?: string | null
  source?: 'status_history' | 'estimated' | null
  now: number
  timezone: string
}

export function RepairSla({ deadline, queuedAt, now, timezone }: RepairSlaProps) {
  const parsed = deadline ? Date.parse(deadline) : Number.NaN
  const queued = queuedAt ? Date.parse(queuedAt) : Number.NaN
  const unknownReason = Number.isFinite(queued)
    ? 'Часовой пояс парка при входе в очередь не подтверждён'
    : 'Нет данных о начале очереди'
  let text = 'SLA: —'
  if (Number.isFinite(parsed)) {
    const delta = parsed - now
    const hours = delta > 0 ? workingHoursBetween(now, parsed, timezone) : 0
    text = `SLA: ${formatSlaRemaining(hours)}${delta < 0 ? ' · Просрочено' : delta === 0 ? ' · Срок наступил' : ''}`
  }
  return (
    <span aria-live="polite" role="status" title={`SLA: осталось из 5 рабочих часов ежедневно с 09:00 до 21:00 по времени парка${Number.isFinite(parsed) ? '' : `. ${unknownReason}`}`}>
      {text}
    </span>
  )
}
