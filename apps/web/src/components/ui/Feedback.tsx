import type { ReactNode } from 'react'

/**
 * Loading and empty-state primitives shared by every screen.
 *
 * Screens used to render a bare «Загрузка…» string, which made the layout jump
 * once data arrived. Skeletons keep the page shape stable instead.
 */

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="spinner-wrap">
      <span aria-hidden="true" className="spinner" />
      {label && <span className="spinner-label">{label}</span>}
    </span>
  )
}

export function SkeletonLine({ width = '100%' }: { width?: string }) {
  return <span className="skeleton skeleton-line" style={{ width }} />
}

export function SkeletonCard() {
  return (
    <div className="skeleton-card">
      <SkeletonLine width="30%" />
      <SkeletonLine width="85%" />
      <SkeletonLine width="55%" />
    </div>
  )
}

export function SkeletonList({ rows = 3 }: { rows?: number }) {
  return (
    <div aria-busy="true" className="skeleton-list" role="status">
      {Array.from({ length: rows }, (_, index) => (
        <SkeletonCard key={index} />
      ))}
    </div>
  )
}

export function SkeletonKpi({ items = 3 }: { items?: number }) {
  return (
    <div aria-busy="true" className="skeleton-kpi" role="status">
      {Array.from({ length: items }, (_, index) => (
        <div className="skeleton-kpi-item" key={index}>
          <SkeletonLine width="60%" />
          <SkeletonLine width="40%" />
        </div>
      ))}
    </div>
  )
}

/** Empty state with an optional icon, hint and call to action. */
export function EmptyBlock({
  icon,
  title,
  hint,
  action,
}: {
  icon?: ReactNode
  title: string
  hint?: string
  action?: ReactNode
}) {
  return (
    <div className="empty-block">
      {icon && <span className="empty-block-icon" aria-hidden="true">{icon}</span>}
      <p className="empty-block-title">{title}</p>
      {hint && <p className="empty-block-hint">{hint}</p>}
      {action && <div className="empty-block-action">{action}</div>}
    </div>
  )
}
