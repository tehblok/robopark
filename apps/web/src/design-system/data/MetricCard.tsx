import { useId, type ReactElement, type ReactNode } from 'react'
import type { StatusTone } from '../status/StatusBadge'
import '../layout/MasterDetail.css'

export type MetricCardProps = {
  label: ReactNode
  value: ReactNode
  delta?: ReactNode
  tone?: StatusTone
}

export function MetricCard({
  label,
  value,
  delta,
  tone = 'neutral',
}: MetricCardProps): ReactElement {
  const labelId = useId()

  return (
    <dl aria-labelledby={labelId} className="rp-metric-card" data-tone={tone}>
      <dt className="rp-metric-card__label" id={labelId}>{label}</dt>
      <dd className="rp-metric-card__value">{value}</dd>
      {delta ? <dd className="rp-metric-card__delta">{delta}</dd> : null}
    </dl>
  )
}
