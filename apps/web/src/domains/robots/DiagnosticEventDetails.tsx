import type { DiagnosticEvent } from '../../api'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { DIAGNOSTIC_SEVERITIES, isLocalizedEvent } from './diagnosticPresentation'

export function DiagnosticEventDetails({ event }: { event: DiagnosticEvent }) {
  return <>
    <h3>{event.title}</h3>
    <StatusBadge tone={event.severity}>{DIAGNOSTIC_SEVERITIES[event.severity]}</StatusBadge>
    <p>{event.description}</p>
    <p>{isLocalizedEvent(event) ? `Часть робота: ${event.part}` : 'Без локализации'}</p>
    <div><span className="rp-check-event-raw-label">Сигнал Emergency</span><pre className="rp-check-field-lines">{typeof event.raw_value === 'string' ? event.raw_value : JSON.stringify(event.raw_value, null, 2)}</pre></div>
  </>
}
