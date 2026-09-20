import { useState } from 'react'
import { formatDurationHours, moscowWorkingHoursBetween } from '../../lib/timeFormat'

export type RepairSlaProps = {
  deadline?: string | null
  source?: 'status_history' | 'estimated' | null
  now?: number
}

export function RepairSla({ deadline, now }: RepairSlaProps) {
  const [mountedAt] = useState(Date.now)
  const referenceTime = now ?? mountedAt
  const parsed = deadline ? Date.parse(deadline) : Number.NaN
  let text = 'Нет данных о начале очереди'
  if (Number.isFinite(parsed)) {
    const delta = parsed - referenceTime
    const hours = delta >= 0
      ? moscowWorkingHoursBetween(referenceTime, parsed)
      : moscowWorkingHoursBetween(parsed, referenceTime)
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
