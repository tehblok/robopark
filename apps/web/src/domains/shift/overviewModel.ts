import type { Blocker, OperationsOverview } from '../../api'
import type { StatusTone } from '../../design-system/status/StatusBadge'
import { STATUS_LABELS } from '../insights/operations'
import { workListHref } from '../work/workUrl'
import { workingHoursBetween } from '../../lib/timeFormat'

export type OverviewStatusCard = {
  key: string
  label: string
  taskCount: number | null
  selected: boolean
}
export type OverviewAlert = {
  tone: StatusTone
  title: string
  description: string
  taskCount?: number | null
}
export type OverviewAttentionItem = {
  key: string
  summary: string
  status: string
  robot: string | null
  assignee: string | null
  slaDeadline: string | null
  downtimeHours: number | null
  slaWorkingHours: number | null
  overdueHours: number | null
  kind: 'overdue' | 'at_risk' | 'attention'
  href: string
}
export type OperationalOverviewModel = {
  timezone: string
  headline: { active: number | null; overdue: number | null; atRisk: number | null; unknownSla: number | null }
  alerts: OverviewAlert[]
  statusCards: OverviewStatusCard[]
  flow: {
    arrivedTaskCount: number | null
    leftTaskCount: number | null
    backlogTaskCount: number | null
    observedBuckets: number
    expectedBuckets: number
    complete: boolean
  }
  attentionQueue: OverviewAttentionItem[]
  attentionTruncated: boolean
  fullQueueHref: string
  workload: OperationsOverview['workload']
  operatorAccounts: OperationsOverview['operators'] | null
}

const ROLE_STATUS_KEYS: Record<string, readonly string[]> = {
  driver: ['new', 'moving'],
  mechanic: ['queued', 'diagnostics'],
}

function isAccountDiagnosticsRole(role: string): boolean {
  return role === 'admin' || role === 'royal'
}

function visibleStatusKeys(data: OperationsOverview, role: string): readonly string[] {
  return ROLE_STATUS_KEYS[role] ?? data.status_options
    .filter((option) => option.key !== 'all')
    .map((option) => option.key)
}

function attentionItem(
  task: Blocker,
  parkId: number,
  kind: OverviewAttentionItem['kind'],
  overdueHours: number | null = null,
  timing?: NonNullable<OperationsOverview['task_timing']>[number],
): OverviewAttentionItem {
  return {
    key: task.key,
    summary: task.summary,
    status: task.status,
    robot: task.robot,
    assignee: task.assignee?.display?.trim() || null,
    slaDeadline: timing?.sla_deadline ?? null,
    downtimeHours: timing?.downtime_hours ?? null,
    slaWorkingHours: timing?.sla_working_hours ?? null,
    overdueHours,
    kind,
    href: `/work/${encodeURIComponent(task.key)}?park=${parkId}`,
  }
}

function operationalAlerts(data: OperationsOverview, overdueTaskCount: number): OverviewAlert[] {
  const alerts: OverviewAlert[] = []
  if (overdueTaskCount > 0) {
    alerts.push({
      tone: 'critical',
      title: `Показано просроченных: ${overdueTaskCount}`,
      description: data.sla.overdue_truncated
        ? 'Список просроченных задач неполный; общий счёт приведён в сводке. Показанные задачи вынесены в начало очереди внимания.'
        : 'Показанные просроченные задачи вынесены в начало очереди внимания.',
      taskCount: overdueTaskCount,
    })
  }
  if (data.sla.target_hours == null) {
    alerts.push({
      tone: 'warning',
      title: 'Данные SLA недоступны',
      description: 'Пятичасовой норматив действует постоянно, но оценка срока не получена. Обновите обзор и проверьте источник данных.',
    })
  }
  if (!data.flow.complete) {
    alerts.push({
      tone: 'info',
      title: 'Неполное покрытие потока',
      description: `Наблюдается ${data.flow.observed_buckets} из ${data.flow.expected_buckets} интервалов; пропуски не считаются нулём.`,
    })
  }
  return alerts
}

function selectedStatusKeys(data: OperationsOverview, role: string): Set<string> {
  const permitted = visibleStatusKeys(data, role)
  return data.selected_status !== 'all' && permitted.includes(data.selected_status)
    ? new Set([data.selected_status])
    : new Set(permitted)
}

function flowTaskTotal(data: OperationsOverview, kind: 'arrived_count' | 'departed_count'): number | null {
  if (data.flow.observed_buckets === 0 || data.flow.points.length === 0) return null
  return data.flow.points.reduce((sum, point) => sum + point[kind], 0)
}

export function buildOverviewModel(data: OperationsOverview, role: string, now = Date.parse(data.generated_at)): OperationalOverviewModel {
  const selectedStatuses = selectedStatusKeys(data, role)
  const isSelected = (task: Blocker) => selectedStatuses.has(task.bucket)
  const timingByIssue = new Map(data.task_timing?.map((row) => {
    const due = Date.parse(row.sla_deadline ?? '')
    if (row.sla_timezone && Number.isFinite(due) && Number.isFinite(now)) {
      try {
        const hours = now < due ? Math.max(0, 5 - workingHoursBetween(now, due, row.sla_timezone))
          : 5 + workingHoursBetween(due, now, row.sla_timezone)
        return [row.issue_key, { ...row, sla_working_hours: hours }] as const
      } catch { /* Retain the server snapshot when the timezone cannot be interpreted. */ }
    }
    return [row.issue_key, row] as const
  }) ?? [])
  const overdue = data.sla.overdue
    .filter(isSelected)
    .map((task) => attentionItem(task, data.park_id, 'overdue', task.overdue_hours, timingByIssue.get(task.key)))
  const overdueKeys = new Set(overdue.map((task) => task.key))
  const observedAt = now
  const attention = data.tasks
    .filter((task) => isSelected(task) && !overdueKeys.has(task.key))
    .map((task) => {
      const timing = timingByIssue.get(task.key)
      const hours = timing?.sla_working_hours
      const target = data.sla.target_hours
      let kind: OverviewAttentionItem['kind'] = 'attention'
      let overdueHours: number | null = null
      if (typeof hours === 'number' && Number.isFinite(hours)
        && typeof target === 'number' && target > 0) {
        const dueAt = Date.parse(timing?.sla_deadline ?? '')
        if (hours > target || (hours === target && Number.isFinite(observedAt)
          && Number.isFinite(dueAt) && observedAt > dueAt)) {
          kind = 'overdue'
          overdueHours = hours - target
        } else if (hours >= target * 0.8) {
          kind = 'at_risk'
        }
      }
      return attentionItem(task, data.park_id, kind, overdueHours, timing)
    })
    .sort((left, right) => {
      const priority = { overdue: 0, at_risk: 1, attention: 2 }
      if (left.kind !== right.kind) return priority[left.kind] - priority[right.kind]
      if (left.downtimeHours == null) return right.downtimeHours == null ? 0 : 1
      if (right.downtimeHours == null) return -1
      return right.downtimeHours - left.downtimeHours
    })

  return {
    timezone: data.timezone,
    headline: {
      active: typeof data.counts.all === 'number' ? data.counts.all : null,
      overdue: data.sla.overdue_count,
      atRisk: data.sla.at_risk_count,
      unknownSla: typeof data.sla.unknown_count === 'number' ? data.sla.unknown_count : null,
    },
    alerts: operationalAlerts(data, overdue.length + attention.filter(item => item.kind === 'overdue').length),
    statusCards: visibleStatusKeys(data, role).map((key) => {
      const option = data.status_options.find((item) => item.key === key)
      return {
        key,
        label: option?.label && option.label !== key ? option.label : STATUS_LABELS[key] ?? key,
        taskCount: typeof data.counts[key] === 'number' ? data.counts[key] : null,
        selected: data.selected_status === key,
      }
    }),
    flow: {
      arrivedTaskCount: flowTaskTotal(data, 'arrived_count'),
      leftTaskCount: flowTaskTotal(data, 'departed_count'),
      backlogTaskCount: typeof data.counts.all === 'number' ? data.counts.all : null,
      observedBuckets: data.flow.observed_buckets,
      expectedBuckets: data.flow.expected_buckets,
      complete: data.flow.complete,
    },
    attentionQueue: [...overdue, ...attention],
    attentionTruncated: data.tasks_truncated || data.sla.overdue_truncated,
    fullQueueHref: workListHref({
      filters: { status: data.selected_status === 'all' ? undefined : data.selected_status },
      sort: 'oldest',
      page: 1,
    }, data.park_id),
    workload: data.workload,
    operatorAccounts: isAccountDiagnosticsRole(role) ? data.operators : null,
  }
}
