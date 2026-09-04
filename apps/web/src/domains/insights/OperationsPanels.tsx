import { Link } from 'react-router-dom'
import type { OperationsOverview } from '../../api'
import { Panel } from '../../design-system/layout/PageLayout'
import { SLA_BASIS, STATUS_LABELS, moscowDate } from './operations'

function nullableCount(value: number | null | undefined): string {
  return value == null ? 'Нет данных' : String(value)
}

export function OperationsMetrics({ data }: { data: OperationsOverview }) {
  const options = data.status_options.filter((option) => option.key !== 'all')
  return <section aria-label="Текущие показатели" className="rp-insights-metrics">
    {options.map((option) => <div className="rp-insights-metric" key={option.key}>
      <span>{option.label || STATUS_LABELS[option.key] || option.key}</span>
      <strong>{nullableCount(data.counts[option.key])}</strong>
    </div>)}
  </section>
}

export function OperationsTasks({ data }: { data: OperationsOverview }) {
  return <Panel title="Текущие задачи" description={`Нагрузка — ${nullableCount(data.counts.all)} открытых задач в доступной области, а не физические роботы в парке.`}>
    {data.tasks.length ? <ul className="rp-insights-tasks">
      {data.tasks.map((task) => <li key={task.key}>
        <Link aria-label={`Открыть задачу ${task.key}`} to={`/work/${encodeURIComponent(task.key)}?park=${data.park_id}`}>
          <strong>{task.key}</strong><span>{task.summary}</span>
        </Link>
        <span>{task.status}{task.robot ? ` · робот ${task.robot}` : ''}</span>
      </li>)}
    </ul> : <p>Нет задач в выбранных статусах</p>}
    {data.tasks_truncated ? <p className="rp-insights-note">Показаны первые {data.tasks.length} из {data.tasks_total} задач.</p> : null}
  </Panel>
}

export function OperationsSla({ data }: { data: OperationsOverview }) {
  const { sla } = data
  return <Panel title="Просрочки SLA" description={SLA_BASIS}>
    {sla.target_hours == null ? <p>Норматив SLA не задан</p> : <>
      <dl className="rp-insights-summary">
        <div><dt>Норматив</dt><dd>{sla.target_hours} ч</dd></div>
        <div><dt>Под риском</dt><dd>{nullableCount(sla.at_risk_count)}</dd></div>
        <div><dt>Просрочено</dt><dd>{nullableCount(sla.overdue_count)}</dd></div>
        <div><dt>Без даты</dt><dd>{sla.unknown_count}</dd></div>
      </dl>
      {sla.overdue_count === 0 ? <p>Просрочек нет</p> : null}
      {sla.overdue.length ? <ul className="rp-insights-tasks">
        {sla.overdue.map((task) => <li key={task.key}><Link to={`/work/${encodeURIComponent(task.key)}?park=${data.park_id}`}>{task.key} · {task.summary}</Link><span>Возраст {task.age_hours} ч · просрочка {task.overdue_hours} ч</span></li>)}
      </ul> : null}
      {sla.overdue_truncated ? <p className="rp-insights-note">Список просроченных задач ограничен.</p> : null}
    </>}
  </Panel>
}

export function OperationsLeadership({ data }: { data: OperationsOverview }) {
  return <>
    {data.workload ? <Panel title="Нагрузка по ответственным" description="Снимок текущих открытых задач, а не оценка работы людей.">
      <ul className="rp-insights-load">{data.workload.map((row) => <li key={row.login ?? '—'}><strong>{row.display}</strong><span>Открыто: {row.open_count}</span><span>Просрочено: {nullableCount(row.overdue_count)}</span><span>Самая старая: {row.oldest_hours == null ? 'Нет данных' : `${row.oldest_hours} ч`}</span></li>)}</ul>
    </Panel> : null}
    {data.operators ? <Panel title="Учётные записи операторов" description="Сопоставление учётных записей с текущими задачами Tracker.">
      <ul className="rp-insights-load">{data.operators.map((row) => <li key={row.user_id}><strong>{row.username}</strong><span>Tracker: {row.tracker_login ?? 'Не сопоставлен'}</span><span>Открыто: {nullableCount(row.open_count)}</span><span>Просрочено: {nullableCount(row.overdue_count)}</span></li>)}</ul>
    </Panel> : null}
  </>
}

export function OperationsUpdated({ data }: { data: OperationsOverview }) {
  return <p className="rp-insights-note">Снимок: {moscowDate(data.generated_at)} · время Москвы (Europe/Moscow)</p>
}
