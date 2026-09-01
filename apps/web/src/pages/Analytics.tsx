import { Link } from 'react-router-dom'
import { api } from '../api'
import { PageShell, Panel } from '../components/PageShell'
import { EmptyBlock, SkeletonKpi } from '../components/ui/Feedback'
import { useAuth } from '../auth-context'
import { ru } from '../i18n/ru'
import { useCachedResource } from '../lib/resource'
import { useParkContext } from '../park-context'
import { OperatorNowReport } from './OperatorNowReport'

function analyticsHint(role: string): string {
  switch (role) {
    case 'mechanic':
      return 'Здесь появится аналитика по вашему парку: скорость закрытия задач и повторные обращения.'
    case 'admin':
    case 'royal':
      return 'Полные дашборды SLA появятся позже. Пока — снимок текущего дня по выбранному парку.'
    default:
      return 'Раздел аналитики находится в разработке.'
  }
}

type DashboardAnalyticsProps = {
  role: string
  permissions: string[]
  parkId: number | null
  parksLoading: boolean
}

function DashboardAnalytics({
  role,
  permissions,
  parkId,
  parksLoading,
}: DashboardAnalyticsProps) {
  const hasDashboard = permissions.includes('nav.dashboard')
  const summaryRes = useCachedResource(
    parkId == null ? '' : `dashboard:summary:${parkId}`,
    () => api.dashboardSummary(parkId as number),
    { enabled: parkId != null && !parksLoading && hasDashboard },
  )
  const summary = summaryRes.data ?? null

  return (
    <PageShell subtitle="Раздел в разработке" title={ru.nav.analytics}>
      {hasDashboard && parkId != null && (
        <Panel hint="Те же цифры, что на дашборде — удобно сверить нагрузку, не уходя с раздела." title="Сегодня">
          {summaryRes.isLoading && !summary ? (
            <SkeletonKpi />
          ) : summary ? (
            <div className="dashboard-kpi-grid">
              <div className="dashboard-kpi tone-arrived">
                <span className="dashboard-kpi-label">Пришли</span>
                <span className="dashboard-kpi-value">{summary.arrived}</span>
              </div>
              <div className="dashboard-kpi tone-done">
                <span className="dashboard-kpi-label">Ушли</span>
                <span className="dashboard-kpi-value">{summary.done}</span>
              </div>
              <div className="dashboard-kpi tone-queued">
                <span className="dashboard-kpi-label">В очереди</span>
                <span className="dashboard-kpi-value">{summary.queued}</span>
              </div>
            </div>
          ) : (
            <EmptyBlock icon="📋" title="Нет данных за сегодня" />
          )}
        </Panel>
      )}

      <EmptyBlock
        action={
          hasDashboard ? (
            <Link className="btn btn-secondary" to="/dashboard">
              Перейти на дашборд
            </Link>
          ) : undefined
        }
        hint={analyticsHint(role)}
        icon="◔"
        title="Расширенная аналитика скоро появится"
      />
    </PageShell>
  )
}

export function Analytics() {
  const { user } = useAuth()
  const { parkId, parksLoading } = useParkContext()

  if (user?.role === 'operator') {
    return <OperatorNowReport />
  }

  return (
    <DashboardAnalytics
      parkId={parkId}
      parksLoading={parksLoading}
      permissions={user?.permissions ?? []}
      role={user?.role ?? ''}
    />
  )
}
