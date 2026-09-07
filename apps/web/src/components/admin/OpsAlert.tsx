import type { ReactNode } from 'react'
import { Icon } from '../../design-system/icons/Icon'
import './OpsAlert.css'

export function OpsAlert({ children, tone = 'warning' }: { children: ReactNode; tone?: 'error' | 'warning' | 'info' }) {
  return <div className="ops-alert" data-tone={tone} role={tone === 'error' ? 'alert' : 'status'}><Icon name={tone === 'error' ? 'critical' : tone === 'info' ? 'info' : 'warning'} size={20} /><span>{children}</span></div>
}
