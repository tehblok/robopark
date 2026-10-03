import { useId, type ReactElement, type ReactNode } from 'react'
import type { StatusTone } from '../status/StatusBadge'
import '../layout/MasterDetail.css'

export type MetricCardProps = {
  label: ReactNode
  value: ReactNode
  valueTitle?: string
  delta?: ReactNode
  tone?: StatusTone
  variant?: 'default' | 'prominent'
}

export function MetricCard({
  label,
  value,
  valueTitle,
  delta,
  tone = 'neutral',
  variant = 'default',
}: MetricCardProps): ReactElement {
  const labelId = useId()

  return (
    <dl aria-labelledby={labelId} className="rp-metric-card" data-tone={tone} data-variant={variant}>
      <dt className="rp-metric-card__label" id={labelId}>{label}</dt>
      <dd className="rp-metric-card__value" title={valueTitle}>{value}</dd>
      {delta ? <dd className="rp-metric-card__delta">{delta}</dd> : null}
    </dl>
  )
}
