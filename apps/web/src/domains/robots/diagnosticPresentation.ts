import type { DiagnosticEvent, DiagnosticIndicator, DiagnosticView, EmergencyReadingValue } from '../../api'
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

type SelectableReading = Pick<EmergencyReadingValue, 'id' | 'section_id' | 'state' | 'view'>
const READING_STATE_ORDER = { critical: 0, warning: 1, normal: 2, unavailable: 3 }

export function chooseAutomaticDiagnosticSelection(
  events: readonly DiagnosticEvent[],
  readings: readonly SelectableReading[],
  blockIds: readonly string[],
): { blockId: string | null; view: DiagnosticView; eventId: string | null } {
  const leading = leadingDiagnosticEvent(events)
  if (leading) {
    const relevant = readings.find(reading => reading.view === leading.view && blockIds.includes(reading.section_id))
    return { blockId: relevant?.section_id ?? blockIds[0] ?? null, view: leading.view, eventId: leading.id }
  }
  const leadingReading = readings
    .filter(reading => blockIds.includes(reading.section_id))
    .map((reading, index) => ({ reading, index }))
    .sort((a, b) => READING_STATE_ORDER[a.reading.state] - READING_STATE_ORDER[b.reading.state] || a.index - b.index)[0]?.reading
  return { blockId: leadingReading?.section_id ?? blockIds[0] ?? null, view: leadingReading?.view ?? 'top', eventId: null }
}
