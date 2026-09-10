import type { OperationsFlow, OperationsOverview, Park, User } from '../../api'

export type OperationsApiClient = {
  operationsOverview(parkId: number, days: number, status: string): Promise<OperationsOverview>
}
export type OperationsQuery = { days: number; status: string }
export const STATUS_LABELS: Record<string, string> = {
  all: 'Все доступные', new: 'Новые', moving: 'Перемещение', queued: 'Очередь',
  diagnostics: 'Диагностика', waiting_team: 'Ожидает команду', waiting_parts: 'Ожидает запчасти', other: 'Другие',
}
export function allowedStatuses(role: string): string[] {
  return role === 'driver' ? ['all', 'new', 'moving'] : Object.keys(STATUS_LABELS)
}
export function parseOperationsQuery(params: URLSearchParams, role: string): OperationsQuery {
  const rawDays = params.get('days')
  const days = rawDays && ['1', '7', '30'].includes(rawDays) ? Number(rawDays) : 7
  const status = params.get('status') ?? 'all'
  return { days, status: allowedStatuses(role).includes(status) ? status : 'all' }
}
export function operationsSearch(current: URLSearchParams, query: OperationsQuery): URLSearchParams {
  const next = new URLSearchParams(current)
  next.delete('days'); next.delete('status')
  if (query.days !== 7) next.set('days', String(query.days))
  if (query.status !== 'all') next.set('status', query.status)
  return next
}
export function operationsAccessIdentity(user: User, selectedPark?: Park | null): string {
  const scope = (park: Park) => [park.id, park.tag?.trim(), park.tracker_queue?.trim(), park.is_active !== false]
  return JSON.stringify([user.id, user.username, user.tracker_login, user.role, user.access_status, Boolean(user.must_change_password), [...new Set(user.permissions ?? [])].sort(), [...user.parks].sort((a, b) => a.id - b.id).map(scope), selectedPark ? scope(selectedPark) : null])
}
export function canReadOperations(user: User, section: 'overview' | 'analytics'): boolean {
  const permissions = user.permissions ?? []
  return user.access_status === 'approved' && !user.must_change_password && permissions.includes('tracker.read') && permissions.includes(section === 'overview' ? 'nav.dashboard' : 'nav.analytics')
}
export const SLA_BASIS = 'SLA: календарные часы с создания задачи в Tracker, 24/7, без вычета пауз. Риск с 80% норматива; просрочка — строго после норматива.'
export function moscowDate(value: string | number): string {
  return new Intl.DateTimeFormat('ru-RU', { timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(value))
}
export function flowBuckets(flow: OperationsFlow) {
  const points = new Map(flow.points.map(point => [Date.parse(point.bucket_start), point]))
  const rows: { time: number; point: OperationsFlow['points'][number] | null }[] = []
  for (let time = Date.parse(flow.window_start); time < Date.parse(flow.window_end); time += 2 * 60 * 60 * 1000) rows.push({ time, point: points.get(time) ?? null })
  return rows
}
