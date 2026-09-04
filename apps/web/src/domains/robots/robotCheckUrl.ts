import type { EmergencySection } from '../../api'
import type { AccessUser } from '../../app/routing/accessPolicy'
import { classifyApiError, type DomainError } from '../../shared/api/classifyApiError'
export type RobotCheckTab = { id: string; title: string; kind: 'map' | 'telemetry' | 'scheme' | 'section' }
export const STATIC_CHECK_TABS: readonly RobotCheckTab[] = [
  { id: 'map', title: 'Карта', kind: 'map' },
  { id: 'telemetry', title: 'Телеметрия', kind: 'telemetry' },
  { id: 'scheme', title: 'Схема', kind: 'scheme' },
]
export function checkTabs(sections: EmergencySection[]): RobotCheckTab[] {
  const seen = new Set(STATIC_CHECK_TABS.map(t => t.id))
  return [...STATIC_CHECK_TABS, ...sections.filter(section => {
    if (!section.id || seen.has(section.id)) return false
    seen.add(section.id); return true
  }).map(section => ({ ...section, kind: 'section' as const }))]
}
export function parseRobotCheckTab(params: URLSearchParams, sections: EmergencySection[]): string {
  const requested = params.get('tab')?.trim() || 'map'
  return checkTabs(sections).some(t => t.id === requested) ? requested : 'map'
}
export function buildRobotCheckSearch(current: URLSearchParams, tab: string): string {
  const next = new URLSearchParams()
  const park = current.get('park')
  if (park && /^\d+$/.test(park)) next.set('park', park)
  if (tab !== 'map') next.set('tab', tab)
  return next.size ? `?${next}` : ''
}

// Route/request ownership includes effective authorization, not the selected park.
export function checkAccessIdentity(user: AccessUser): string {
  return JSON.stringify([user.role, user.access_status, user.must_change_password, [...(user.permissions ?? [])].sort(), user.parks.filter(p => p.is_active !== false).map(p => [p.id, p.tag, p.tracker_queue]).sort((a, b) => Number(a[0]) - Number(b[0]))])
}
export function classifyCheckError(error: unknown): DomainError {
  const failure = classifyApiError(error, 'Не удалось загрузить данные проверки робота.')
  return failure.kind === 'configuration' ? { ...failure, title: 'Интеграция проверки робота требует внимания', description: 'Обратитесь к администратору для проверки подключения.' } : failure
}
