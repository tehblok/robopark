import { useEffect, useState } from 'react'
import type { EmergencySnapshot } from '../../api'
import { Button } from '../../design-system/actions/Button'
import { StaleBadge } from '../../design-system/feedback/AsyncState'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { leadingDiagnosticEvent } from './diagnosticPresentation'
import { buildRobotDetailModel } from './robotDetailModel'
import { RobotQrButton } from './RobotQrButton'

export function RobotCheckSummary({ snapshot, online, failed, pending, onRefresh, onShowDiagnostic }: { snapshot: EmergencySnapshot; online: boolean; failed: boolean; pending: boolean; onRefresh: () => void; onShowDiagnostic: () => void }) {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const clock = globalThis.setInterval(() => setNow(new Date()), 30_000)
    return () => globalThis.clearInterval(clock)
  }, [])
  const model = buildRobotDetailModel(snapshot, online, now)
  const leading = leadingDiagnosticEvent(snapshot.diagnostic_events ?? [])
  const observed = new Date(snapshot.observed_at)
  const date = Number.isNaN(observed.getTime()) ? 'Дата неизвестна' : new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' }).format(observed).replace(/\s*г\.$/, '')
  const time = Number.isNaN(observed.getTime()) ? '' : new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(observed)
  const retry = !online || failed
  return <section className="rp-check-summary" aria-label="Состояние робота" aria-busy={pending}>
    <h2>Робот {model.shortNumber}</h2>
    <StatusBadge tone={model.connection.tone}>{model.connection.label}</StatusBadge>
    <p className="rp-check-charge">Заряд {snapshot.charge_percent == null ? 'нет данных' : `${snapshot.charge_percent} %`}</p>
    <StaleBadge state={failed ? 'stale' : model.freshness} updatedAt={snapshot.observed_at} />
    <p><time dateTime={snapshot.observed_at}>Данные на {date}{time ? ` · ${time}` : ''}</time></p>
    {leading ? <div className="rp-check-leading-diagnostic">
      <strong>{leading.title}</strong>
      <p>{leading.description}</p>
      <Button variant="secondary" onClick={onShowDiagnostic}>Показать неисправность</Button>
    </div> : null}
    {model.criticalReason ? <p className="rp-check-critical" role="status">{model.criticalReason}</p> : null}
    <details className="rp-check-supplementary"><summary>VIN и координаты</summary>
      <p className="rp-check-vin">{snapshot.vin}</p>
      <p>{snapshot.lat != null && snapshot.lon != null ? `${snapshot.lat}, ${snapshot.lon}` : 'Координаты не получены'}</p>
      <RobotQrButton vin={snapshot.vin} />
    </details>
    {retry ? <Button onClick={onRefresh}>Повторить проверку</Button> : null}
  </section>
}
