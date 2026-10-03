import { Link } from 'react-router-dom'
import { MetricCard } from '../../design-system/data/MetricCard'
import { EntityRow } from '../../design-system/data/EntityRow'
import { EmptyState } from '../../design-system/feedback/AsyncState'
import { Panel } from '../../design-system/layout/PageLayout'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { trackerStatusLabel } from '../../components/tracker/trackerStatusLabel'
import { formatDurationHours, formatParkDateTime } from '../../lib/timeFormat'
import { formatSlaRemaining } from '../work/repairSlaFormat'
import type { OperationalOverviewModel } from './overviewModel'

function taskCount(value: number | null): string {
  return value == null ? 'Нет данных' : `${value} задач`
}

function overdueLabel(hours: number | null): string {
  if (hours == null) return 'Просрочка: неизвестна'
  if (hours < 0.05) return 'Срок SLA истёк'
  return `Просрочено на ${formatDurationHours(hours)}`
}

export function OverviewHeadline({ headline }: Pick<OperationalOverviewModel, 'headline'>) {
  const display = (value: number | null) => value == null ? '—' : value
  const unknownTitle = (value: number | null) => value == null ? 'Не измерено' : undefined
  return <section aria-label="Сводка смены" className="rp-overview-headline">
      <MetricCard label="Активные задачи" value={display(headline.active)} valueTitle={unknownTitle(headline.active)} variant="prominent" />
      <MetricCard label="Просрочено SLA" tone={headline.overdue ? 'critical' : 'neutral'} value={display(headline.overdue)} valueTitle={unknownTitle(headline.overdue)} variant="prominent" />
      <MetricCard label="Риск SLA" tone={headline.atRisk ? 'warning' : 'neutral'} value={display(headline.atRisk)} valueTitle={unknownTitle(headline.atRisk)} variant="prominent" />
      <MetricCard label="SLA не определён" tone={headline.unknownSla ? 'warning' : 'neutral'} value={display(headline.unknownSla)} valueTitle={unknownTitle(headline.unknownSla)} variant="prominent" />
  </section>
}

export function OverviewAlerts({ alerts }: Pick<OperationalOverviewModel, 'alerts'>) {
  if (!alerts.length) return null

  return <Panel title="Что требует внимания" description="Сигналы из текущего снимка смены."><div aria-label="Оповещения смены" className="rp-overview-alerts" data-testid="overview-alerts">
    {alerts.map((alert) => <div className="rp-overview-alert" data-tone={alert.tone} key={alert.title} role={alert.tone === 'critical' ? 'alert' : undefined}>
      <StatusBadge tone={alert.tone}>{alert.title}</StatusBadge>
      <p>{alert.description}</p>
    </div>)}
  </div></Panel>
}

export function OverviewStatusMonitoring({ statusCards, statusHref, allHref, selectable }: Pick<OperationalOverviewModel, 'statusCards'> & { statusHref: (status: string) => string; allHref: string | null; selectable: boolean }) {
  return <Panel density="summary" title="Статусы задач" description={selectable ? 'Выберите статус, чтобы сузить очередь внимания до разрешённых вашей роли задач.' : 'Текущий состав разрешённых вашей роли задач.'}>
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

export function OverviewAttentionQueue({ attentionQueue, attentionTruncated, fullQueueHref, timezone }: Pick<OperationalOverviewModel, 'attentionQueue' | 'attentionTruncated' | 'fullQueueHref' | 'timezone'>) {
  return <Panel title="Очередь решений" description="Сначала просрочки и риск SLA. Отсчёт от 5 часов до нуля, ежедневно с 09:00 до 21:00 по времени парка.">
    {attentionQueue.length ? <div className="rp-overview-task-table-wrap" data-testid="overview-attention-queue"><table className="rp-overview-task-table">
      <caption>Задачи смены</caption>
      <colgroup><col className="rp-overview-col-task" /><col className="rp-overview-col-status" /><col className="rp-overview-col-sla" /></colgroup>
      <thead><tr><th scope="col">Задача и робот</th><th scope="col">Этап и исполнитель</th><th scope="col">SLA</th></tr></thead>
      <tbody>{attentionQueue.map(item => <tr data-tone={item.kind === 'overdue' ? 'critical' : item.kind === 'at_risk' ? 'warning' : 'neutral'} key={item.key}>
        <th scope="row"><strong>{item.key}</strong><span>{item.summary}</span>{item.robot ? <small>Робот {item.robot}</small> : null}<Link aria-label={`Открыть задачу ${item.key}`} to={item.href}>Открыть задачу</Link></th>
        <td><span className="rp-overview-task-mobile-label">Этап и исполнитель</span><StatusBadge tone="neutral">{trackerStatusLabel(item.status)}</StatusBadge><small>{item.assignee ?? 'Не назначен'}</small></td>
        <td><span className="rp-overview-task-mobile-label">SLA</span>{item.kind === 'overdue' ? <StatusBadge tone="critical">Просрочено SLA</StatusBadge> : item.kind === 'at_risk' ? <StatusBadge tone="warning">Риск SLA</StatusBadge> : null}<span>SLA: {item.slaWorkingHours == null ? '—' : formatSlaRemaining(5 - item.slaWorkingHours)}</span><small>Срок: {formatParkDateTime(item.slaDeadline, timezone) ?? 'Неизвестен'}</small>{item.kind === 'overdue' ? <small>{overdueLabel(item.overdueHours)}</small> : null}</td>
      </tr>)}</tbody>
    </table></div> : <EmptyState description="В текущей выборке нет доступных задач." icon="work" title="Нет задач в очереди внимания" />}
    {attentionTruncated ? <p className="rp-overview-note">Список задач ограничен данными текущего ответа.</p> : null}
    <div className="rp-overview-queue-actions"><Link className="rp-button rp-button--secondary rp-button--compact" to={fullQueueHref}>Вся очередь в «Работе»</Link></div>
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
