import type { DiagnosticEvent, DiagnosticIndicator, DiagnosticView } from '../../api'
import { ROBOT_PHOTOS } from './robotPhotos'

type LocalizedEvent = DiagnosticEvent & { part: string; view: DiagnosticView; x: number; y: number; indicator: DiagnosticIndicator }
const SEVERITY_ORDER = { critical: 0, warning: 1, info: 2 }
export const DIAGNOSTIC_SEVERITIES = { critical: 'Критическая ошибка', warning: 'Предупреждение', info: 'Информация' }

export function isLocalizedEvent(event: DiagnosticEvent): event is LocalizedEvent {
  return event.rule_id != null && Boolean(event.part) && ROBOT_PHOTOS.some(photo => photo.id === event.view)
    && typeof event.x === 'number' && Number.isFinite(event.x) && event.x >= 0 && event.x <= 1
    && typeof event.y === 'number' && Number.isFinite(event.y) && event.y >= 0 && event.y <= 1
    && ['point', 'outline', 'zone'].includes(event.indicator ?? '')
}
export function leadingDiagnosticEvent(events: readonly DiagnosticEvent[]): LocalizedEvent | undefined {
  // Stable IDs also keep equal-order choices fixed if a legacy server reorders a poll.
  return events.filter(isLocalizedEvent).sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity]
    || a.sort_order - b.sort_order || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))[0]
}
export function chooseAutomaticView(events: readonly DiagnosticEvent[]): DiagnosticView {
  return leadingDiagnosticEvent(events)?.view ?? 'top'
}
