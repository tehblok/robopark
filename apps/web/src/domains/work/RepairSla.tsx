export type RepairSlaProps = {
  deadline?: string | null
  source?: 'status_history' | 'estimated' | null
  now?: number
}

const FALLBACK_NOW = Date.now()

function durationText(minutes: number): string {
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  if (hours && rest) return `${hours} ч ${rest} мин`
  if (hours) return `${hours} ч`
  return `${rest} мин`
}

export function RepairSla({ deadline, source, now = FALLBACK_NOW }: RepairSlaProps) {
  const parsed = deadline ? Date.parse(deadline) : Number.NaN
  let text = 'SLA: нет данных'
  if (Number.isFinite(parsed)) {
    const delta = parsed - now
    const minutes = Math.ceil(Math.abs(delta) / 60_000)
    text = delta >= 0
      ? `Осталось ${durationText(minutes)}`
      : `Просрочено на ${durationText(minutes)}`
  }
  return (
    <span aria-live="polite" role="status">
      {text}{source === 'estimated' ? ' · Срок рассчитан приблизительно' : ''}
    </span>
  )
}
