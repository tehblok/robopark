import type { ReactNode } from 'react'
import { Button } from '../actions/Button'
import { Icon, type IconName } from '../icons/Icon'
import { StatusBadge } from '../status/StatusBadge'
import './AsyncState.css'

export type Freshness = 'live' | 'fresh' | 'stale' | 'offline'

export function LoadingState({ label, variant = 'panel' }: { label: string; variant?: 'inline' | 'panel' | 'page' }) {
  return (
    <div className={`rp-async-state rp-loading-state rp-loading-state--${variant}`} role="status" aria-label={label} aria-busy="true">
      <span className="rp-loading-state__indicator" aria-hidden="true" />
      <span>{label}</span>
    </div>
  )
}

export function EmptyState({ title, description, icon, action }: { title: string; description?: string; icon?: IconName; action?: ReactNode }) {
  return (
    <section className="rp-async-state rp-empty-state">
      {icon ? <Icon name={icon} size={32} aria-hidden="true" /> : null}
      <h2>{title}</h2>
      {description ? <p>{description}</p> : null}
      {action ? <div className="rp-async-state__action">{action}</div> : null}
    </section>
  )
}

export function ErrorState({ title, description, onRetry, retryLabel = 'Повторить', requestId }: { title: string; description: string; onRetry?: () => void; retryLabel?: string; requestId?: string }) {
  return (
    <section className="rp-async-state rp-error-state" role="alert">
      <h2>{title}</h2>
      <p>{description}</p>
      {requestId ? <small>Код запроса: {requestId}</small> : null}
      {onRetry ? <div className="rp-async-state__action"><Button variant="secondary" leadingIcon="refresh" onClick={onRetry}>{retryLabel}</Button></div> : null}
    </section>
  )
}

const FRESHNESS = {
  live: { icon: 'success', tone: 'success', text: 'Данные актуальны' },
  fresh: { icon: 'success', tone: 'success', text: 'Данные свежие' },
  stale: { icon: 'warning', tone: 'warning', text: 'Данные устарели' },
  offline: { icon: 'offline', tone: 'warning', text: 'Нет связи с источником' },
} as const satisfies Record<Freshness, { icon: IconName; tone: 'success' | 'warning'; text: string }>

export function StaleBadge({ state, updatedAt, label }: { state: Freshness; updatedAt?: string | null; label?: string }) {
  const freshness = FRESHNESS[state]
  const date = updatedAt ? new Date(updatedAt) : null
  const time = date && !Number.isNaN(date.getTime())
    ? new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' }).format(date)
    : null
  const text = label ? `${label}: ${freshness.text}` : freshness.text

  return (
    <StatusBadge tone={freshness.tone} icon={freshness.icon} className="rp-freshness-badge">
      <span>{text}{time ? ` · ${time}` : ''}</span>
    </StatusBadge>
  )
}
