import { useId, useLayoutEffect, useRef, useState } from 'react'
import type { DiagnosticEvent } from '../../api'
import { Button } from '../../design-system/actions/Button'
import { ROBOT_PHOTOS, type RobotPhotoId } from './robotPhotos'
import { DiagnosticEventDetails } from './DiagnosticEventDetails'
import { DIAGNOSTIC_SEVERITIES, isLocalizedEvent, leadingDiagnosticEvent } from './diagnosticPresentation'

const VIEWS: { id: RobotPhotoId; label: string }[] = [
  { id: 'top', label: 'Сверху' }, { id: 'front', label: 'Спереди' }, { id: 'rear', label: 'Сзади' },
  { id: 'left', label: 'Слева' }, { id: 'right', label: 'Справа' }, { id: 'isometric', label: 'Изометрия' },
]
const WHEEL_LABELS: Record<string, string> = {
  fl: 'Переднее левое колесо', ml: 'Среднее левое колесо', rl: 'Заднее левое колесо',
  fr: 'Переднее правое колесо', mr: 'Среднее правое колесо', rr: 'Заднее правое колесо',
}

type Props = {
  faults: string[]; events: DiagnosticEvent[]; view: RobotPhotoId; selectedEventId: string | null
  onViewChange: (view: RobotPhotoId) => void; onSelectEvent: (event: DiagnosticEvent) => void
  onShowError: () => void; onRevealEvent: (event: DiagnosticEvent) => void; onOpenErrors: () => void
}
export function RobotDiagnosticDiagram({ faults, events, view, selectedEventId, onViewChange, onSelectEvent, onShowError, onRevealEvent, onOpenErrors }: Props) {
  const detailId = useId()
  const detailRef = useRef<HTMLElement>(null)
  const [revealRevision, setRevealRevision] = useState(0)
  useLayoutEffect(() => {
    // Only an activation scrolls. Focus, initial load and polling keep their position.
    const detail = detailRef.current
    if (!revealRevision || !detail) return
    // The fixed shell navigation overlays the viewport. Measure its actual frame,
    // including safe-area padding, so reveal also works after viewport/inset changes.
    const navigation = detail.closest('.rp-app-shell')?.querySelector('.rp-shell__bottom-nav')?.getBoundingClientRect()
    const occlusion = navigation?.height ? Math.max(0, window.innerHeight - navigation.top) : 0
    detail.style.setProperty('--rp-check-bottom-occlusion', `${occlusion}px`)
    detail.scrollIntoView?.({ block: 'nearest' })
  }, [revealRevision])
  const [failedImages, setFailedImages] = useState<Partial<Record<RobotPhotoId, boolean>>>({})
  const photo = ROBOT_PHOTOS.find(item => item.id === view)!
  const knownFaults = [...new Set(faults.filter(slot => Object.hasOwn(WHEEL_LABELS, slot)))]
  const hasUnlocalizedFault = faults.some(slot => !Object.hasOwn(WHEEL_LABELS, slot))
  const failed = failedImages[view]
  const selectedEvent = events.find(event => event.id === selectedEventId)
  const markers = events.filter(isLocalizedEvent).filter(event => event.view === view)
  return <div className="rp-check-diagram">
    <div className="rp-check-views" role="group" aria-label="Ракурс модели">
      {VIEWS.map(item => <button type="button" key={item.id} aria-pressed={view === item.id} onClick={() => onViewChange(item.id)}>{item.label}</button>)}
    </div>
    {events.length ? <div className="rp-check-event-actions">
      <Button variant="secondary" disabled={!leadingDiagnosticEvent(events)} onClick={() => { onShowError(); setRevealRevision(current => current + 1) }}>Показать ошибку</Button>
      <Button variant="secondary" onClick={onOpenErrors}>Все ошибки ({events.length})</Button>
    </div> : null}
    <div className="rp-check-diagram-content" data-events={Boolean(events.length)}>
    <figure>
      <div className="rp-check-photo-frame">
        {failed ? <svg role="img" aria-label="Схема модели робота" viewBox="0 0 240 320">
          <rect className="rp-check-diagram-body" height="220" rx="36" width="160" x="40" y="50" />
          <rect className="rp-check-diagram-lid" height="54" rx="18" width="112" x="64" y="24" />
          <circle className="rp-check-diagram-sensor" cx="120" cy="51" r="10" />
          <path className="rp-check-diagram-divider" d="M56 160h128M120 78v176" />
        </svg> : <img key={photo.id} src={photo.src} alt={`Иллюстрация модели робота: ${photo.title.toLowerCase()}`} width={photo.width} height={photo.height} loading="lazy" decoding="async" onError={() => setFailedImages(current => ({ ...current, [view]: true }))} />}
        {!failed ? markers.map(event => <button type="button" key={event.id}
          className={`rp-check-event-marker rp-check-event-marker--${event.indicator} rp-check-event-marker--${event.severity}`}
          style={{ left: `clamp(var(--rp-marker-inset), ${event.x * 100}%, calc(100% - var(--rp-marker-inset)))`, top: `clamp(var(--rp-marker-inset), ${event.y * 100}%, calc(100% - var(--rp-marker-inset)))` }}
          aria-label={`Ошибка: ${event.title}`} aria-pressed={selectedEventId === event.id} aria-controls={detailId}
          aria-describedby={`${detailId}-${event.id}`} onFocus={() => onSelectEvent(event)} onClick={() => { onSelectEvent(event); setRevealRevision(current => current + 1) }}>
          <span aria-hidden="true">{event.severity === 'info' ? 'i' : '!'}</span>
        </button>) : null}
      </div>
      <figcaption>Иллюстрация модели</figcaption>
    </figure>
    <div className="rp-check-diagnostic-details">
      {markers.map(event => <span className="rp-check-event-description" key={event.id} id={`${detailId}-${event.id}`}>{event.description} {event.part}. {DIAGNOSTIC_SEVERITIES[event.severity]}</span>)}
      {failed && events.length ? <p role="status">Фотография не загрузилась. Маркеры ошибок доступны только на фотографиях; расшифровки сохранены.</p> : null}
      {selectedEvent ? <section ref={detailRef} id={detailId} className="rp-check-event-detail" aria-label="Выбранная ошибка" aria-live="polite">
        <DiagnosticEventDetails event={selectedEvent} />
        {isLocalizedEvent(selectedEvent) && selectedEvent.view !== view ? <Button variant="secondary" onClick={() => onRevealEvent(selectedEvent)}>Посмотреть на схеме</Button> : null}
      </section> : events.length ? <p id={detailId}>Выберите маркер ошибки на фотографии.</p> : null}
    </div>
    </div>
    <div className="rp-check-wheel-details" aria-live="polite">
      {knownFaults.length ? <ul>{knownFaults.map(slot => <li key={slot}>Неисправность: {WHEEL_LABELS[slot]}</li>)}</ul> : null}
      {hasUnlocalizedFault ? <p>Неисправность колёс: точное расположение не определено</p> : null}
      {!faults.length ? <p>Сообщений о неисправностях колёс нет. Это не подтверждает исправность всех компонентов.</p> : null}
    </div>
  </div>
}
