import { useState } from 'react'
import { Button } from '../../design-system/actions/Button'
import { ROBOT_PHOTOS, type RobotPhotoId } from './robotPhotos'
import { photoWheelHotspots, splitWheelFaults, WHEEL_LABELS, type PhotoWheelSlot } from './robotPhotoHotspots'

const VIEWS: { id: RobotPhotoId; label: string }[] = [
  { id: 'top', label: 'Сверху' }, { id: 'front', label: 'Спереди' }, { id: 'rear', label: 'Сзади' },
  { id: 'left', label: 'Слева' }, { id: 'right', label: 'Справа' }, { id: 'isometric', label: 'Изометрия' },
]

export function RobotDiagnosticDiagram({ faults, onSelectWheels }: { faults: string[]; onSelectWheels?: () => void }) {
  const [view, setView] = useState<RobotPhotoId>('top')
  const [failedImages, setFailedImages] = useState<Partial<Record<RobotPhotoId, boolean>>>({})
  const [selected, setSelected] = useState<PhotoWheelSlot | null>(null)
  const photo = ROBOT_PHOTOS.find(item => item.id === view)!
  const failure = splitWheelFaults(faults)
  const failed = failedImages[view]
  // Fallback has its own original SVG frame, never the photo coordinate system.
  const hotspots = failed ? photoWheelHotspots('top').map(h => ({ ...h, x: h.x < .5 ? .16 : .84, y: h.slot.startsWith('f') ? .28 : h.slot.startsWith('m') ? .5 : .72 })) : photoWheelHotspots(view)
  return <div className="rp-check-diagram">
    <div className="rp-check-views" role="group" aria-label="Ракурс модели">
      {VIEWS.map(item => <button type="button" key={item.id} aria-pressed={view === item.id} onClick={() => setView(item.id)}>{item.label}</button>)}
    </div>
    <figure>
      <div className="rp-check-photo-frame">
        {failed ? <svg role="img" aria-label="Схема модели робота" viewBox="0 0 240 320">
          <rect className="rp-check-diagram-body" height="220" rx="36" width="160" x="40" y="50" />
          <rect className="rp-check-diagram-lid" height="54" rx="18" width="112" x="64" y="24" />
          <circle className="rp-check-diagram-sensor" cx="120" cy="51" r="10" />
          <path className="rp-check-diagram-divider" d="M56 160h128M120 78v176" />
        </svg> : <img key={photo.id} src={photo.src} alt={`Иллюстрация модели робота: ${photo.title.toLowerCase()}`} width={photo.width} height={photo.height} loading="lazy" decoding="async" onError={() => setFailedImages(current => ({ ...current, [view]: true }))} />}
        {hotspots.map(h => {
          const fault = failure.known.includes(h.slot)
          return <button className={`rp-check-wheel${fault ? ' rp-check-wheel--fault' : ''}`} type="button" key={h.slot}
            style={{ left: `${h.x * 100}%`, top: `${h.y * 100}%` }}
            aria-label={`${WHEEL_LABELS[h.slot]}: ${fault ? 'неисправность' : 'ошибка не сообщена'}`} aria-pressed={selected === h.slot}
            onClick={() => setSelected(h.slot)}><span aria-hidden="true">{fault ? '!' : '·'}</span></button>
        })}
      </div>
      <figcaption>Иллюстрация модели</figcaption>
    </figure>
    {view === 'isometric' && !failed ? <Button variant="secondary" onClick={() => setView('top')}>Показать колёса сверху</Button> : null}
    <div className="rp-check-wheel-details" aria-live="polite">
      {selected ? <p>Выбрано: {WHEEL_LABELS[selected]}</p> : <p>Выберите колесо на иллюстрации.</p>}
      {failure.known.length ? <ul>{failure.known.map(slot => <li key={slot}>Неисправность: {WHEEL_LABELS[slot]}</li>)}</ul> : null}
      {failure.unlocalized ? <p>Неисправность колёс: точное расположение не определено</p> : null}
      {!faults.length ? <p>Сообщений о неисправностях колёс нет. Это не подтверждает исправность всех компонентов.</p> : null}
      {selected && onSelectWheels ? <Button variant="secondary" onClick={onSelectWheels}>Открыть данные колёс</Button> : null}
    </div>
  </div>
}
