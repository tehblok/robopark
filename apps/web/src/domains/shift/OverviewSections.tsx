import { Link } from 'react-router-dom'
import { EmptyState, StaleBadge } from '../../design-system/feedback/AsyncState'
import { Icon } from '../../design-system/icons/Icon'
import { Panel } from '../../design-system/layout/PageLayout'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import type { OverviewViewModel } from './overviewModel'

export function OverviewScope({
  scope,
  updatedAt,
  freshness,
}: Pick<OverviewViewModel, 'scope' | 'updatedAt' | 'freshness'>) {
  return (
    <div className="rp-overview-scope" data-testid="overview-scope">
      <p>{scope}</p>
      {freshness && updatedAt ? (
        <StaleBadge state={freshness} updatedAt={updatedAt} />
      ) : null}
    </div>
  )
}

export function OverviewState({ state }: Pick<OverviewViewModel, 'state'>) {
  return (
    <div className="rp-overview-state" data-testid="overview-state">
      <Panel title={state.title}>
        <StatusBadge tone={state.tone}>Состояние смены</StatusBadge>
        <p className="rp-overview-description">{state.description}</p>
      </Panel>
    </div>
  )
}

export function OverviewRiskCard({ risk }: Pick<OverviewViewModel, 'risk'>) {
  if (!risk) return null

  return (
    <div className="rp-overview-risk" data-testid="overview-risk" data-tone={risk.tone}>
      <Panel title={risk.title}>
        <StatusBadge tone={risk.tone}>Приоритет внимания</StatusBadge>
        <p className="rp-overview-description">{risk.description}</p>
      </Panel>
    </div>
  )
}

export function OverviewPrimaryAction({
  primaryAction,
}: Pick<OverviewViewModel, 'primaryAction'>) {
  if (!primaryAction) return null

  return (
    <div className="rp-overview-primary" data-testid="overview-action">
      <Link className="rp-overview-primary__link" to={primaryAction.href}>
        <Icon name={primaryAction.icon} size={20} />
        <span>{primaryAction.label}</span>
        <Icon name="forward" size={18} />
      </Link>
    </div>
  )
}

export function OverviewQueue({ queue }: Pick<OverviewViewModel, 'queue'>) {
  return (
    <div className="rp-overview-queue" data-testid="overview-queue">
      <Panel title="Ближайшая работа">
        {queue.length ? (
          <ul className="rp-overview-queue__list">
            {queue.map((item) => (
              <li key={item.href}>
                <Link
                  aria-label={`Открыть задачу ${item.key}: ${item.summary}`}
                  className="rp-overview-queue__link"
                  to={item.href}
                >
                  <span className="rp-overview-queue__key">{item.key}</span>
                  <span>{item.summary}</span>
                  {item.robot ? <span className="rp-overview-queue__robot">Робот {item.robot}</span> : null}
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState
            description="В текущей выборке нет доступных задач."
            icon="work"
            title="Нет задач в обзоре"
          />
        )}
      </Panel>
    </div>
  )
}

export function OverviewMetrics({ metrics }: Pick<OverviewViewModel, 'metrics'>) {
  if (!metrics.length) return null

  return (
    <dl aria-label="Текущие показатели" className="rp-overview-metrics" data-testid="overview-metrics">
      {metrics.map((metric) => (
        <div className="rp-overview-metric" key={metric.label}>
          <dt>{metric.label}</dt>
          <dd>{metric.value}</dd>
        </div>
      ))}
    </dl>
  )
}
