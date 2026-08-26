import type { EmergencySnapshot } from '../../api'
import { ru } from '../../i18n/ru'
import robotTop from '../../assets/robot-top.png'
import { WHEEL_HOTSPOTS, type WheelSlot } from './wheelHotspots'

type RobotSchematicProps = {
  snapshot: EmergencySnapshot | null
  dimmed?: boolean
  onWheelClick: (slot: WheelSlot) => void
  onSoundClick?: () => void
  onPowerClick?: () => void
}

function formatSpeed(speed: number | null | undefined): string {
  if (speed == null || Number.isNaN(speed)) return '—'
  return speed.toFixed(1)
}

function formatPercent(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—'
  return `${Math.round(value)}%`
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

function NavIcon() {
  return (
    <svg aria-hidden className="inspection-icon" viewBox="0 0 24 24">
      <path d="M12 3l8 18-8-4-8 4z" fill="currentColor" />
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

function SpeakerIcon() {
  return (
    <svg aria-hidden viewBox="0 0 24 24">
      <path
        d="M4 9v6h4l5 4V5L8 9H4zm13.5 3a3.5 3.5 0 0 0-1.8-3.05v6.1A3.5 3.5 0 0 0 17.5 12z"
        fill="currentColor"
      />
    </svg>
  )
}

function BoltIcon() {
  return (
    <svg aria-hidden viewBox="0 0 24 24">
      <path d="M13 2L4 14h6l-1 8 11-14h-6l-1-6z" fill="currentColor" />
    </svg>
  )
}

function batteryTone(value: number | null | undefined): 'ok' | 'warn' | 'muted' {
  if (value == null) return 'muted'
  if (value < 20) return 'warn'
  return 'ok'
}

function diskTone(value: number | null | undefined): 'ok' | 'warn' | 'muted' {
  if (value == null) return 'muted'
  if (value >= 80) return 'warn'
  return 'ok'
}

export function RobotSchematic({
  snapshot,
  dimmed = false,
  onWheelClick,
  onSoundClick,
  onPowerClick,
}: RobotSchematicProps) {
  const heading = snapshot?.heading_deg
  const showHud = snapshot != null && !dimmed
  const mode = snapshot?.mode ?? '—'
  const modeActive = Boolean(snapshot?.mode && /auto/i.test(snapshot.mode))
  const icpLabel = snapshot?.icp_label ?? 'ICP'
  const lteLabel = snapshot?.lte_label ?? 'LTE'
  const speed = formatSpeed(snapshot?.speed)
  const bat1 = snapshot?.battery1_percent
  const bat2 = snapshot?.battery2_percent
  const disk = snapshot?.disk_percent
  const banner = snapshot?.error_banner
  const offline = snapshot?.online === false
  const noLink = snapshot?.online == null && showHud

  return (
    <aside className={`inspection-sidebar${dimmed ? ' is-dimmed' : ''}`}>
      {showHud && (
        <div className="inspection-hud">
          <div className="inspection-hud-row inspection-hud-row-3">
            <div className={`inspection-tile${modeActive ? ' is-mode-on' : ''}`}>
              <strong>{mode}</strong>
            </div>
            <div
              className={`inspection-tile inspection-tile-icon${
                snapshot?.icp_ok === false ? ' is-bad' : snapshot?.icp_ok ? ' is-good' : ''
              }`}
            >
              <NavIcon />
              <span>{icpLabel}</span>
            </div>
            <div
              className={`inspection-tile inspection-tile-icon${
                snapshot?.lte_ok === false || offline ? ' is-bad' : snapshot?.lte_ok ? ' is-good' : ''
              }`}
            >
              <SignalIcon />
              <span>{lteLabel}</span>
            </div>
          </div>

          <div className="inspection-hud-row inspection-hud-row-4">
            <div className="inspection-tile inspection-tile-metric">
              <strong>{speed}</strong>
              <span>{ru.emergency.speedUnit}</span>
            </div>
            <div className="inspection-tile inspection-tile-metric">
              <BatteryIcon tone={batteryTone(bat1)} />
              <span>{formatPercent(bat1)}</span>
            </div>
            <div className="inspection-tile inspection-tile-metric">
              <BatteryIcon tone={batteryTone(bat2)} />
              <span>{formatPercent(bat2)}</span>
            </div>
            <div className="inspection-tile inspection-tile-metric">
              <DiskIcon tone={diskTone(disk)} />
              <span>{formatPercent(disk)}</span>
            </div>
          </div>
        </div>
      )}

      {showHud && (banner || offline || noLink) && (
        <div className={`inspection-banner${offline || noLink ? ' is-offline' : ''}`}>
          {banner
            ?? (offline ? ru.emergency.offlineBanner : ru.emergency.noLinkBanner)}
        </div>
      )}

      <div className="inspection-robot">
        <div
          className="inspection-photo-body"
          style={{
            transform: heading != null ? `rotate(${heading}deg)` : undefined,
          }}
        >
          <img alt="Робот" src={robotTop} />
          {showHud &&
            WHEEL_HOTSPOTS.map((hotspot) => (
              <button
                aria-label={ru.emergency.wheelSlots[hotspot.slot] ?? hotspot.slot}
                className={`inspection-hotspot${
                  snapshot?.wheels_fault.includes(hotspot.slot) ? ' is-fault' : ''
                }`}
                key={hotspot.slot}
                onClick={() => onWheelClick(hotspot.slot)}
                style={{ top: hotspot.top, left: hotspot.left }}
                type="button"
              />
            ))}
          {showHud && snapshot?.wheels_fault.includes('body') && (
            <span
              className="inspection-hotspot is-fault"
              style={{ top: '50%', left: '50%', transform: 'translate(-50%, -50%)' }}
            />
          )}
        </div>

        <div className="inspection-actions">
          <button
            aria-label={ru.emergency.soundAction}
            className="inspection-action"
            disabled={!onSoundClick || dimmed}
            onClick={onSoundClick}
            type="button"
          >
            <SpeakerIcon />
          </button>
          <button
            aria-label={ru.emergency.powerAction}
            className="inspection-action"
            disabled={!onPowerClick || dimmed}
            onClick={onPowerClick}
            type="button"
          >
            <BoltIcon />
          </button>
        </div>
      </div>
    </aside>
  )
}
