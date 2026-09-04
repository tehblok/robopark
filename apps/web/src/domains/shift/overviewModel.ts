import type { DashboardSummary, Park } from '../../api'
import { isSystemUserRole, type UserRole } from '../../app/routing/routeManifest'
import type { Freshness } from '../../design-system/feedback/AsyncState'
import type { IconName } from '../../design-system/icons/Icon'
import type { StatusTone } from '../../design-system/status/StatusBadge'
import { workIssueHref, workListHref, type WorkUrlState } from '../work/workUrl'
import type { OverviewPayload } from './overviewData'

export type OverviewAction = { label: string; href: string; icon: IconName }
export type OverviewRisk = {
  tone: StatusTone
  title: string
  description: string
  issueKey?: string
}
export type OverviewQueueItem = {
  key: string
  summary: string
  robot?: string | null
  href: string
}
export type OverviewViewModel = {
  scope: string
  updatedAt: string | null
  freshness: Freshness | null
  state: { title: string; description: string; tone: StatusTone }
  risk: OverviewRisk | null
  primaryAction: OverviewAction | null
  queue: OverviewQueueItem[]
  metrics: Array<{ label: string; value: number }>
}
export type OverviewModelInput = {
  role: string
  payload: OverviewPayload
  parkId: number | null
  canOpenAdministration: boolean
}

const ROLE_STATE_TITLE: Record<UserRole, string> = {
  mechanic: 'Что требует внимания в смене',
  operator: 'Что мешает работе парка',
  driver: 'Можно ли безопасно продолжать работу',
  admin: 'Готовность людей и системы',
  royal: 'Главный риск доступных парков',
}

export function overviewStateTitle(role: string): string {
  return isSystemUserRole(role)
    ? ROLE_STATE_TITLE[role]
    : 'Что требует внимания сейчас'
}

function validTimestamp(timestamp: string): string | null {
  return Number.isFinite(Date.parse(timestamp)) ? timestamp : null
}

function freshnessAt(updatedAt: string | null, now: Date): Freshness | null {
  if (!updatedAt || !Number.isFinite(now.getTime())) return null
  const age = now.getTime() - Date.parse(updatedAt)
  if (age <= 30_000) return 'live'
  if (age <= 300_000) return 'fresh'
  return 'stale'
}

function oldestTimestamp(timestamps: string[]): string | null {
  let oldest: string | null = null
  let oldestTime = Number.POSITIVE_INFINITY
  for (const timestamp of timestamps) {
    const time = Date.parse(timestamp)
    if (!Number.isFinite(time)) return null
    if (time < oldestTime) {
      oldest = timestamp
      oldestTime = time
    }
  }
  return oldest
}

type CurrentCounts = Pick<DashboardSummary, 'arrived' | 'done' | 'queued' | 'in_transit'>

function currentMetrics(counts: CurrentCounts): OverviewViewModel['metrics'] {
  return [
    { label: 'Пришли', value: counts.arrived },
    { label: 'Завершены', value: counts.done },
    { label: 'В очереди', value: counts.queued },
    { label: 'В пути', value: counts.in_transit },
  ]
}

function workState(park?: Park): WorkUrlState {
  const queue = park?.tracker_queue?.trim()
  return { filters: queue ? { queue } : {}, sort: 'oldest', page: 1 }
}

function queueItem(
  item: { key: string; summary: string; robot?: string | null },
  park: Park,
): OverviewQueueItem {
  return {
    key: item.key,
    summary: item.summary,
    ...(item.robot === undefined ? {} : { robot: item.robot }),
    href: workIssueHref(item.key, workState(park), park.id),
  }
}

function issueActionLabel(role: string, key: string): string {
  if (role === 'mechanic') return `Открыть задачу ${key}`
  if (role === 'operator') return `Разобрать ${key}`
  if (role === 'admin') return 'Открыть работу'
  return `Открыть ${key}`
}

export function buildOverviewModel(
  { role, payload, canOpenAdministration }: OverviewModelInput,
  now: Date,
): OverviewViewModel {
  if (role === 'driver' || payload.kind === 'driver') {
    return {
      scope: 'Область работы: проверка конкретного робота',
      updatedAt: null,
      freshness: null,
      state: {
        title: overviewStateTitle('driver'),
        description: 'Найдите или сканируйте робота, чтобы проверить его состояние перед продолжением работы.',
        tone: 'info',
      },
      risk: null,
      primaryAction: { label: 'Найти или сканировать робота', href: '/robots', icon: 'scan' },
      queue: [],
      metrics: [],
    }
  }

  if (payload.kind === 'fleet') {
    const { summaries } = payload
    const highest = summaries.reduce<(typeof summaries)[number] | null>(
      (current, item) => !current || item.summary.queued > current.summary.queued ? item : current,
      null,
    )
    const totals = summaries.reduce<CurrentCounts>((counts, item) => ({
      arrived: counts.arrived + item.summary.arrived,
      done: counts.done + item.summary.done,
      queued: counts.queued + item.summary.queued,
      in_transit: counts.in_transit + item.summary.in_transit,
    }), { arrived: 0, done: 0, queued: 0, in_transit: 0 })
    const updatedAt = oldestTimestamp(summaries.map((item) => item.summary.generated_at))

    return {
      scope: `Все доступные активные парки · ${summaries.length}`,
      updatedAt,
      freshness: freshnessAt(updatedAt, now),
      state: {
        title: overviewStateTitle(role),
        description: highest
          ? 'Текущая сводка всех доступных активных парков.'
          : 'Нет доступных активных парков.',
        tone: 'neutral',
      },
      risk: highest && highest.summary.queued > 0 ? {
        tone: 'info',
        title: `Наибольшая текущая очередь: ${highest.park.name} · ${highest.summary.queued}`,
        description: 'Текущий размер очереди. Откройте работу парка, чтобы уточнить задачи и приоритет.',
      } : null,
      primaryAction: highest ? {
        label: `Открыть работу парка ${highest.park.name}`,
        href: workListHref(workState(), highest.park.id),
        icon: 'work',
      } : null,
      queue: summaries.flatMap((item) =>
        item.summary.moving.map((moving) => queueItem(moving, item.park)),
      ),
      metrics: highest ? currentMetrics(totals) : [],
    }
  }

  const { park, summary, issues } = payload
  const configured = Boolean(park.tracker_queue?.trim() && park.tag?.trim())
  const nextIssue = summary.moving[0] ?? issues[0]
  const updatedAt = validTimestamp(summary.generated_at)
  const risk: OverviewRisk | null = !configured ? {
    tone: 'warning',
    title: 'Парк не готов к работе с Tracker',
    description: canOpenAdministration
      ? 'Укажите очередь Tracker и тег парка в настройках управления.'
      : 'Обратитесь к администратору: для парка нужно указать очередь Tracker и тег.',
  } : nextIssue ? {
    tone: 'info',
    title: `Ближайшая задача: ${nextIssue.key}`,
    description: nextIssue.summary,
    issueKey: nextIssue.key,
  } : null
  const primaryAction: OverviewAction | null = !configured
    ? canOpenAdministration
      ? { label: 'Настроить парк', href: '/admin', icon: 'settings' }
      : null
    : nextIssue
      ? {
          label: issueActionLabel(role, nextIssue.key),
          href: workIssueHref(nextIssue.key, workState(park), park.id),
          icon: 'work',
        }
      : { label: 'Открыть работу', href: workListHref(workState(park), park.id), icon: 'work' }

  return {
    scope: `Парк: ${park.name}`,
    updatedAt,
    freshness: freshnessAt(updatedAt, now),
    state: {
      title: overviewStateTitle(role),
      description: configured
        ? 'Текущая сводка парка и ближайшие задачи из рабочей очереди.'
        : 'Для работы парка требуется настройка Tracker.',
      tone: configured ? 'neutral' : 'warning',
    },
    risk,
    primaryAction,
    queue: issues.map((item) => queueItem(item, park)),
    metrics: currentMetrics(summary),
  }
}
