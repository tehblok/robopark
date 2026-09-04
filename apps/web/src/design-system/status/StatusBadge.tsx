import type { ReactElement, ReactNode } from 'react'
import { Icon, type IconName } from '../icons/Icon'
import './StatusBadge.css'

export type StatusTone = 'neutral' | 'info' | 'success' | 'warning' | 'critical'

export type StatusBadgeProps = {
  tone: StatusTone
  icon?: IconName
  children: ReactNode
  className?: string
}

const DEFAULT_ICONS = {
  neutral: 'info',
  info: 'info',
  success: 'success',
  warning: 'warning',
  critical: 'critical',
} satisfies Record<StatusTone, IconName>

export function StatusBadge({
  tone,
  icon = DEFAULT_ICONS[tone],
  children,
  className = '',
}: StatusBadgeProps): ReactElement {
  return (
    <span className={`rp-status-badge ${className}`.trim()} data-tone={tone}>
      <Icon name={icon} size={16} />
      {children}
    </span>
  )
}
