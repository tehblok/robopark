import { useId, useLayoutEffect, useRef, useState } from 'react'
import type { DiagnosticEvent, EmergencyReadingValue, EmergencySection } from '../../api'
import { Button } from '../../design-system/actions/Button'
import { DiagnosticEventDetails } from './DiagnosticEventDetails'
import { DIAGNOSTIC_SEVERITIES, isLocalizedEvent, leadingDiagnosticEvent } from './diagnosticPresentation'
import { placeLabels, type LabelBox } from './readingPlacement'
import { ROBOT_PHOTOS, type RobotPhotoId } from './robotPhotos'

const VIEWS: { id: RobotPhotoId; label: string }[] = [
  { id: 'top', label: 'Сверху' }, { id: 'front', label: 'Спереди' }, { id: 'rear', label: 'Сзади' },
  { id: 'left', label: 'Слева' }, { id: 'right', label: 'Справа' }, { id: 'isometric', label: 'Изометрия' },
]
const WHEEL_LABELS: Record<string, string> = {
  fl: 'Переднее левое колесо', ml: 'Среднее левое колесо', rl: 'Заднее левое колесо',
  fr: 'Переднее правое колесо', mr: 'Среднее правое колесо', rr: 'Заднее правое колесо',
}

type Props = {
  faults: string[]; events: DiagnosticEvent[]; readings: EmergencyReadingValue[]; blocks: EmergencySection[]
  selectedBlockId: string | null; view: RobotPhotoId; selectedEventId: string | null
  onViewChange: (view: RobotPhotoId) => void; onBlockChange: (blockId: string) => void
  onSelectEvent: (event: DiagnosticEvent) => void; onShowError: () => void
  onRevealEvent: (event: DiagnosticEvent) => void; onOpenErrors: () => void
}
type Measurement = { width: number; height: number; robot: DOMRectReadOnly }

export function RobotDiagnosticDiagram({ faults, events, readings, blocks, selectedBlockId, view, selectedEventId, onViewChange, onBlockChange, onSelectEvent, onShowError, onRevealEvent, onOpenErrors }: Props) {
  const detailId = useId()
  const detailRef = useRef<HTMLElement>(null)
  const frameRef = useRef<HTMLDivElement>(null)
  const robotRef = useRef<HTMLDivElement>(null)
  const [measurement, setMeasurement] = useState<Measurement | null>(null)
  const [selectedReadingId, setSelectedReadingId] = useState<number | null>(null)
  const [revealRevision, setRevealRevision] = useState(0)
  useLayoutEffect(() => {
    const frame = frameRef.current
    const robot = robotRef.current
    if (!frame || !robot) return
    const measure = () => {
      const outer = frame.getBoundingClientRect()
      const inner = robot.getBoundingClientRect()
      setMeasurement({ width: outer.width, height: outer.height, robot: {
        width: inner.width,
        height: inner.height,
        x: inner.left - outer.left,
        y: inner.top - outer.top,
        left: inner.left - outer.left,
        right: inner.right - outer.left,
        top: inner.top - outer.top,
        bottom: inner.bottom - outer.top,
        toJSON: () => ({}),
      } as DOMRectReadOnly })
    }
    measure()
    if (!globalThis.ResizeObserver) return
    const observer = new ResizeObserver(measure)
    observer.observe(frame); observer.observe(robot)
    return () => observer.disconnect()
  }, [view])
  useLayoutEffect(() => {
    const detail = detailRef.current
    if (!revealRevision || !detail) return
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
  const eventMarkers = events.filter(isLocalizedEvent).filter(event => event.view === view)
  const selectedBlock = blocks.find(block => block.id === selectedBlockId) ?? blocks[0]
  const blockReadings = readings.filter(reading => reading.section_id === selectedBlock?.id)
  const readingMarkers = blockReadings.filter(reading => reading.view === view)
  const labels = measurement ? [
    ...eventMarkers.map(event => ({ id: `event:${event.id}`, text: `${event.title}: ${event.description}`, x: measurement.robot.left + event.x * measurement.robot.width, y: measurement.robot.top + event.y * measurement.robot.height, direction: 'auto' as const, priority: 'error' as const })),
    ...readingMarkers.filter(reading => reading.state === 'critical' || reading.state === 'warning' || reading.id === selectedReadingId).map(reading => ({ id: `reading:${reading.id}`, text: `${reading.label}: ${reading.display}`, x: measurement.robot.left + reading.x * measurement.robot.width, y: measurement.robot.top + reading.y * measurement.robot.height, direction: reading.label_direction, priority: reading.state === 'critical' ? 'critical' as const : reading.id === selectedReadingId ? 'selected' as const : 'warning' as const })),
  ] : []
  const boxes: LabelBox[] = labels.map(label => ({ ...label, width: Math.min(190, Math.max(96, label.text.length * 6)), height: 42 }))
  const placed = measurement ? placeLabels(boxes, measurement, measurement.robot) : []
  const collapsed = placed.filter(label => label.collapsed)
  return <div className="rp-check-diagram">
    <div className="rp-check-views" role="group" aria-label="Ракурс модели">
      {VIEWS.map(item => <button type="button" key={item.id} aria-pressed={view === item.id} onClick={() => onViewChange(item.id)}>{item.label}</button>)}
    </div>
    {events.length ? <div className="rp-check-event-actions">
      <Button variant="secondary" disabled={!leadingDiagnosticEvent(events)} onClick={() => { onShowError(); setRevealRevision(current => current + 1) }}>Показать ошибку</Button>
      <Button variant="secondary" onClick={onOpenErrors}>Все ошибки ({events.length})</Button>
    </div> : null}
    <div className="rp-check-diagram-content" data-events={Boolean(events.length || readings.length)}>
      <figure>
        <div ref={frameRef} className="rp-check-overlay-frame">
          <div ref={robotRef} className="rp-check-photo-frame">
            {failed ? <svg role="img" aria-label="Схема модели робота" viewBox="0 0 240 320"><rect className="rp-check-diagram-body" height="220" rx="36" width="160" x="40" y="50" /><rect className="rp-check-diagram-lid" height="54" rx="18" width="112" x="64" y="24" /><circle className="rp-check-diagram-sensor" cx="120" cy="51" r="10" /><path className="rp-check-diagram-divider" d="M56 160h128M120 78v176" /></svg>
              : <img key={photo.id} src={photo.src} alt={`Иллюстрация модели робота: ${photo.title.toLowerCase()}`} width={photo.width} height={photo.height} loading="lazy" decoding="async" onError={() => setFailedImages(current => ({ ...current, [view]: true }))} />}
            {!failed ? eventMarkers.map(event => <button type="button" key={event.id} className={`rp-check-event-marker rp-check-event-marker--${event.indicator} rp-check-event-marker--${event.severity}`} style={{ left: `${event.x * 100}%`, top: `${event.y * 100}%` }} aria-label={`Ошибка: ${event.title}`} aria-pressed={selectedEventId === event.id} aria-controls={detailId} aria-describedby={`${detailId}-${event.id}`} onFocus={() => onSelectEvent(event)} onClick={() => { onSelectEvent(event); setRevealRevision(current => current + 1) }}><span className="rp-check-marker-dot" aria-hidden="true">{event.severity === 'info' ? 'i' : '!'}</span></button>) : null}
            {!failed ? readingMarkers.map(reading => <button type="button" key={reading.id} className={`rp-check-reading-marker rp-check-reading-marker--${reading.state}`} style={{ left: `${reading.x * 100}%`, top: `${reading.y * 100}%` }} aria-label={`Показание: ${reading.label}, ${reading.display}`} aria-pressed={selectedReadingId === reading.id} onClick={() => setSelectedReadingId(current => current === reading.id ? null : reading.id)}><span className="rp-check-marker-dot" aria-hidden="true" /></button>) : null}
          </div>
          {measurement ? <svg className="rp-check-leaders" aria-hidden="true" viewBox={`0 0 ${measurement.width} ${measurement.height}`}>{placed.filter(label => !label.collapsed).map(label => <line key={label.id} x1={label.x} y1={label.y} x2={label.left + label.width / 2} y2={label.top + label.height / 2} />)}</svg> : null}
          {placed.filter(label => !label.collapsed).map(label => <span key={label.id} className="rp-check-marker-label" style={{ left: label.left, top: label.top, width: label.width }}>{labels.find(item => item.id === label.id)?.text}</span>)}
        </div>
        <figcaption>Иллюстрация модели</figcaption>
        {collapsed.length ? <ol className="rp-check-collapsed-labels" aria-label="Метки рядом со схемой">{collapsed.map(label => <li key={label.id}>{labels.find(item => item.id === label.id)?.text}</li>)}</ol> : null}
      </figure>
      <div className="rp-check-diagnostic-details">
        <div className="rp-check-block-selector" role="group" aria-label="Блок диагностики">{blocks.map(block => <button type="button" key={block.id} aria-expanded={block.id === selectedBlock?.id} onClick={() => onBlockChange(block.id)}>{block.title}</button>)}</div>
        {selectedBlock ? <section className="rp-check-diagnostic-block" aria-label={`Диагностический блок «${selectedBlock.title}»`}><h3>{selectedBlock.title}</h3>{blockReadings.length ? <dl>{blockReadings.map(reading => <div key={reading.id} data-state={reading.state}><dt>{reading.label}</dt><dd>{reading.display}</dd></div>)}</dl> : <p>Настроенных показаний нет.</p>}</section> : null}
        {eventMarkers.map(event => <span className="rp-check-event-description" key={event.id} id={`${detailId}-${event.id}`}>{event.description} {event.part}. {DIAGNOSTIC_SEVERITIES[event.severity]}</span>)}
        {failed && events.length ? <p role="status">Фотография не загрузилась. Маркеры ошибок доступны только на фотографиях; расшифровки сохранены.</p> : null}
        {selectedEvent ? <section ref={detailRef} id={detailId} className="rp-check-event-detail" aria-label="Выбранная ошибка" aria-live="polite"><DiagnosticEventDetails event={selectedEvent} />{isLocalizedEvent(selectedEvent) && selectedEvent.view !== view ? <Button variant="secondary" onClick={() => onRevealEvent(selectedEvent)}>Посмотреть на схеме</Button> : null}</section> : events.length ? <p id={detailId}>Выберите маркер ошибки на фотографии.</p> : null}
      </div>
    </div>
    <div className="rp-check-wheel-details" aria-live="polite">{knownFaults.length ? <ul>{knownFaults.map(slot => <li key={slot}>Неисправность: {WHEEL_LABELS[slot]}</li>)}</ul> : null}{hasUnlocalizedFault ? <p>Неисправность колёс: точное расположение не определено</p> : null}{!faults.length ? <p>Сообщений о неисправностях колёс нет. Это не подтверждает исправность всех компонентов.</p> : null}</div>
  </div>
}
