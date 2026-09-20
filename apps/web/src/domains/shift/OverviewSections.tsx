import { Link } from 'react-router-dom'
import { MetricCard } from '../../design-system/data/MetricCard'
import { EntityRow } from '../../design-system/data/EntityRow'
import { EmptyState } from '../../design-system/feedback/AsyncState'
import { Panel } from '../../design-system/layout/PageLayout'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { trackerStatusLabel } from '../../components/tracker/trackerStatusLabel'
import { formatDurationHours } from '../../lib/timeFormat'
import type { OperationalOverviewModel } from './overviewModel'

function taskCount(value: number | null): string {
  return value == null ? 'Нет данных' : `${value} задач`
}

export function OverviewAlerts({ alerts }: Pick<OperationalOverviewModel, 'alerts'>) {
  if (!alerts.length) return null

  return <section aria-label="Оповещения смены" className="rp-overview-alerts" data-testid="overview-alerts">
    {alerts.map((alert) => <div className="rp-overview-alert" data-tone={alert.tone} key={alert.title} role={alert.tone === 'critical' ? 'alert' : undefined}>
      <StatusBadge tone={alert.tone}>{alert.title}</StatusBadge>
      <p>{alert.description}</p>
    </div>)}
  </section>
}

export function OverviewStatusMonitoring({ statusCards, statusHref, allHref, selectable }: Pick<OperationalOverviewModel, 'statusCards'> & { statusHref: (status: string) => string; allHref: string | null; selectable: boolean }) {
  return <Panel className="a-overview-status-panel" density="summary" title="Статусы задач" description={selectable ? 'Выберите статус, чтобы сузить очередь внимания до разрешённых вашей роли задач.' : 'Текущий состав разрешённых вашей роли задач.'}>
    <div className="rp-overview-statuses" data-testid="overview-statuses">
      {allHref ? <Link aria-label="Все разрешённые задачи" className="rp-overview-status rp-overview-status--all" to={allHref}>Все разрешённые задачи</Link> : null}
      {statusCards.map((card) => selectable
        ? <Link aria-current={card.selected ? 'page' : undefined} aria-label={`${card.label}: ${taskCount(card.taskCount)}`} className="rp-overview-status" key={card.key} to={statusHref(card.key)}>
          <MetricCard label={card.label} tone={card.selected ? 'info' : 'neutral'} value={taskCount(card.taskCount)} />
        </Link>
        : <div className="rp-overview-status" key={card.key}>
          <MetricCard label={card.label} tone="neutral" value={taskCount(card.taskCount)} />
        </div>) }
    </div>
  </Panel>
}

export function OverviewFlow({ flow }: Pick<OperationalOverviewModel, 'flow'>) {
  return <Panel density="dense" title="Поток задач: пришло / ушло" description="Компактный операционный срез: созданные и решённые задачи в наблюдаемых интервалах, не физические перемещения роботов.">
    <div aria-label="Сводка потока задач" className="rp-overview-flow">
      <MetricCard label="Пришло задач" value={taskCount(flow.arrivedTaskCount)} />
      <MetricCard label="Ушло задач" tone="success" value={taskCount(flow.leftTaskCount)} />
      <MetricCard label="В работе задач" tone="info" value={taskCount(flow.backlogTaskCount)} />
    </div>
    <p className="rp-overview-note">Покрытие: {flow.observedBuckets} из {flow.expectedBuckets} интервалов.{flow.complete ? '' : ' Пробелы не считаются нулями.'}</p>
  </Panel>
}

export function OverviewAttentionQueue({ attentionQueue, attentionTruncated }: Pick<OperationalOverviewModel, 'attentionQueue' | 'attentionTruncated'>) {
  return <Panel className="a-overview-attention-panel" title="Очередь внимания" description="Сначала просрочки SLA, затем задачи по возрасту. Это задачи Tracker, а не количество роботов.">
    {attentionQueue.length ? <div className="rp-overview-entities" data-testid="overview-attention-queue">
      {attentionQueue.map((item) => <EntityRow
        actions={<Link aria-label={`Открыть задачу ${item.key}`} to={item.href}>Открыть</Link>}
        key={item.key}
        meta={<>{item.robot ? `Робот ${item.robot} · ` : ''}{item.kind === 'overdue' ? `Просрочено на ${item.overdueHours == null ? 'неизвестно' : formatDurationHours(item.overdueHours)}` : item.ageHours == null ? 'Возраст неизвестен' : `Возраст ${formatDurationHours(item.ageHours)}`}</>}
        status={<StatusBadge tone={item.kind === 'overdue' ? 'critical' : 'neutral'}>{item.kind === 'overdue' ? 'Просрочено SLA' : trackerStatusLabel(item.status)}</StatusBadge>}
        title={<><strong>{item.key}</strong><span> · </span><span>{item.summary}</span></>}
      />)}
    </div> : <EmptyState description="В текущей выборке нет доступных задач." icon="work" title="Нет задач в очереди внимания" />}
    {attentionTruncated ? <p className="rp-overview-note">Список задач ограничен данными текущего ответа.</p> : null}
  </Panel>
}

export function OverviewWorkload({ workload }: Pick<OperationalOverviewModel, 'workload'>) {
  if (!workload) return null

  return <Panel title="Нагрузка по ответственным" description="Снимок открытых задач по людям, а не оценка работы сотрудников.">
    <div className="rp-overview-entities">
      {workload.map((person) => <EntityRow
        key={person.login ?? person.display}
        meta={`Открыто: ${taskCount(person.open_count)} · Просрочено: ${taskCount(person.overdue_count)} · Самая старая: ${person.oldest_hours == null ? 'нет данных' : formatDurationHours(person.oldest_hours)}`}
        title={person.display}
      />)}
    </div>
  </Panel>
}

export function OverviewOperatorAccounts({ operatorAccounts }: Pick<OperationalOverviewModel, 'operatorAccounts'>) {
  if (!operatorAccounts) return null

  return <Panel title="Учётные записи операторов" description="Диагностика сопоставления людей с текущими задачами Tracker.">
    <div className="rp-overview-entities">
      {operatorAccounts.map((operator) => <EntityRow
        key={operator.user_id}
        meta={`Tracker: ${operator.tracker_login ?? 'Не сопоставлен'} · Открыто: ${taskCount(operator.open_count)} · Просрочено: ${taskCount(operator.overdue_count)}`}
        title={operator.username}
      />)}
    </div>
  </Panel>
}
