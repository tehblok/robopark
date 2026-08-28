import type { EmergencySnapshot } from '../../api'
import { ru } from '../../i18n/ru'
import robotTop from '../../assets/robot-top.png'
import { WHEEL_HOTSPOTS, type WheelSlot } from './wheelHotspots'
import {
  formatHudPercent,
  formatHudSpeed,
  metricTone,
  robotHudBatteries,
  robotHudConnection,
  robotHudConnectionLabel,
  robotHudStatus,
  robotHudStatusLabel,
  wheelFaultLabel,
} from './robotHud'

type RobotSchematicProps = {
  snapshot: EmergencySnapshot | null
  dimmed?: boolean
  onWheelClick: (slot: WheelSlot) => void
}

function BatteryIcon({ tone = 'ok' }: { tone?: 'ok' | 'warn' | 'muted' }) {
  return (
    <svg aria-hidden className={`inspection-icon is-${tone}`} viewBox="0 0 24 14">
      <rect
        fill="none"
        height="10"
        rx="2"
        stroke="currentColor"
        strokeWidth="1.6"
        width="18"
        x="1"
        y="2"
      />
      <rect fill="currentColor" height="5" rx="0.5" width="2" x="20" y="4.5" />
      <rect fill="currentColor" height="6" rx="1" width="12" x="3.5" y="4" />
    </svg>
  )
}

function DiskIcon({ tone = 'warn' }: { tone?: 'ok' | 'warn' | 'muted' }) {
  return (
    <svg aria-hidden className={`inspection-icon is-${tone}`} viewBox="0 0 24 24">
      <rect
        fill="none"
        height="16"
        rx="2"
        stroke="currentColor"
        strokeWidth="1.6"
        width="16"
        x="4"
        y="4"
      />
      <path d="M8 8h8M8 12h8M8 16h5" fill="none" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  )
}

function SignalIcon() {
  return (
    <svg aria-hidden className="inspection-icon" viewBox="0 0 24 24">
      <path d="M4 16h3v4H4zm5-4h3v8H9zm5-4h3v12h-3zm5-4h3v16h-3z" fill="currentColor" />
    </svg>
  )
}

function WireIcon() {
  return (
    <svg aria-hidden className="inspection-icon" viewBox="0 0 24 24">
      <path
        d="M7 4h3v4H8v3.2a4 4 0 1 0 8 0V8h-2V4h3v5.2a6 6 0 1 1-12 0V4z"
        fill="currentColor"
      />
    </svg>
  )
}

function WheelFaultBadge() {
  return (
    <svg aria-hidden className="inspection-wheel-badge" viewBox="0 0 24 24">
      <circle cx="12" cy="12" r="11" fill="#e11d48" />
      <circle cx="12" cy="12" r="11" fill="none" stroke="#fff" strokeWidth="1.4" />
      <path
        d="M8 8l8 8M16 8l-8 8"
        fill="none"
        stroke="#fff"
        strokeLinecap="round"
        strokeWidth="2.6"
      />
    </svg>
  )
}

export function RobotSchematic({
  snapshot,
  dimmed = false,
  onWheelClick,
}: RobotSchematicProps) {
  const showHud = snapshot != null && !dimmed
  const status = robotHudStatus(snapshot?.online)
  const connection = robotHudConnection(snapshot)
  const batteries = robotHudBatteries(snapshot)
  const speed = formatHudSpeed(snapshot?.speed)
  const disk = snapshot?.disk_percent
  const banner = snapshot?.error_banner
  const offline = snapshot?.online === false
  const noLink = snapshot?.online == null && showHud
  const metricCount = 1 + batteries.length

  return (
    <aside className={`inspection-sidebar${dimmed ? ' is-dimmed' : ''}`}>
      {showHud && (
        <div className="inspection-hud">
          <div className="inspection-hud-row inspection-hud-row-3">
            <div
              className={`inspection-tile${
                status === 'active' ? ' is-mode-on' : status === 'offline' ? ' is-bad' : ''
              }`}
            >
              <strong>{robotHudStatusLabel(status)}</strong>
            </div>
            <div className="inspection-tile inspection-tile-metric">
              <strong>{speed}</strong>
              <span>{ru.emergency.speedUnit}</span>
            </div>
            <div
              className={`inspection-tile inspection-tile-icon${
                connection === 'lte' || connection === 'wire'
                  ? ' is-good'
                  : offline
                    ? ' is-bad'
                    : ''
              }`}
            >
              {connection === 'wire' ? <WireIcon /> : <SignalIcon />}
              <span>{robotHudConnectionLabel(connection)}</span>
            </div>
          </div>

          <div
            className="inspection-hud-row inspection-hud-metrics"
            style={{ gridTemplateColumns: `repeat(${metricCount}, minmax(0, 1fr))` }}
          >
            {batteries.map((pack) => (
              <div className="inspection-tile inspection-tile-metric" key={pack.id}>
                <BatteryIcon tone={metricTone('battery', pack.percent)} />
                <strong>{formatHudPercent(pack.percent)}</strong>
                <span>{pack.label}</span>
              </div>
            ))}
            <div className="inspection-tile inspection-tile-metric">
              <DiskIcon tone={metricTone('disk', disk)} />
              <strong>{formatHudPercent(disk)}</strong>
              <span>{ru.emergency.disk}</span>
            </div>
          </div>
        </div>
      )}

      {showHud && (banner || offline || noLink) && (
        <div className={`inspection-banner${offline || noLink ? ' is-offline' : ''}`}>
          {banner ?? (offline ? ru.emergency.offlineBanner : ru.emergency.noLinkBanner)}
        </div>
      )}

      <div className="inspection-robot">
        <div className="inspection-photo-body">
          <img alt="Робот" src={robotTop} />
          {showHud &&
            WHEEL_HOTSPOTS.map((hotspot) => {
              const fault = snapshot?.wheels_fault.includes(hotspot.slot)
              return (
                <button
                  aria-label={ru.emergency.wheelSlots[hotspot.slot] ?? hotspot.slot}
                  className={`inspection-hotspot is-${hotspot.side}${fault ? ' is-fault' : ''}`}
                  key={hotspot.slot}
                  onClick={() => onWheelClick(hotspot.slot)}
                  style={{ top: hotspot.top, left: hotspot.left }}
                  type="button"
                >
                  {fault && (
                    <>
                      <WheelFaultBadge />
                      <span className="inspection-wheel-label">
                        {wheelFaultLabel(hotspot.slot)}
                      </span>
                    </>
                  )}
                </button>
              )
            })}
          {showHud && snapshot?.wheels_fault.includes('body') && (
            <span className="inspection-hotspot is-fault is-body" style={{ top: '50%', left: '50%' }}>
              <WheelFaultBadge />
              <span className="inspection-wheel-label">{wheelFaultLabel('body')}</span>
            </span>
          )}
        </div>
      </div>
    </aside>
  )
}
