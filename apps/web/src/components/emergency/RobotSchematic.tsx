import type { EmergencySnapshot } from '../../api'
import { ru } from '../../i18n/ru'
import robotTop from '../../assets/robot-top.png'
import { WHEEL_HOTSPOTS, type WheelSlot } from './wheelHotspots'

type RobotSchematicProps = {
  snapshot: EmergencySnapshot | null
  dimmed?: boolean
  onWheelClick: (slot: WheelSlot) => void
}

function formatSpeed(speed: number | null): string {
  return speed == null ? '—' : String(speed)
}

function formatCharge(charge: number | null): string {
  return charge == null ? '—' : `${charge}%`
}

export function RobotSchematic({
  snapshot,
  dimmed = false,
  onWheelClick,
}: RobotSchematicProps) {
  const heading = snapshot?.heading_deg
  const showHud = snapshot != null && !dimmed

  return (
    <div className="inspection-photo" style={dimmed ? { opacity: 0.45 } : undefined}>
      <div
        className="inspection-photo-body"
        style={{
          position: 'relative',
          transform: heading != null ? `rotate(${heading}deg)` : undefined,
        }}
      >
        <img alt="Робот" src={robotTop} />
        {showHud && WHEEL_HOTSPOTS.map((hotspot) => (
          <button
            aria-label={hotspot.slot}
            className={`btn-ghost inspection-hotspot${
              snapshot.wheels_fault.includes(hotspot.slot) ? ' is-fault' : ''
            }`}
            key={hotspot.slot}
            onClick={() => onWheelClick(hotspot.slot)}
            style={{ top: hotspot.top, left: hotspot.left }}
            type="button"
          />
        ))}
        {showHud && snapshot.wheels_fault.includes('body') && (
          <span
            className="inspection-hotspot is-fault"
            style={{ top: '50%', left: '50%', transform: 'translate(-50%, -50%)' }}
          />
        )}
      </div>
      {showHud && (
        <div className="inspection-chips">
          {snapshot.online === false && (
            <span className="inspection-chip">{ru.emergency.offline}</span>
          )}
          {snapshot.online == null && (
            <span className="inspection-chip">{ru.emergency.noLink}</span>
          )}
          <span className="inspection-chip">
            {ru.emergency.speed}: {formatSpeed(snapshot.speed)}
          </span>
          <span className="inspection-chip">
            {ru.emergency.charge}: {formatCharge(snapshot.charge_percent)}
          </span>
        </div>
      )}
    </div>
  )
}
