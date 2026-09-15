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
  const diagnosticEvents = snapshot.diagnostic_events ?? []
  const leading = leadingDiagnosticEvent(diagnosticEvents)
  const hasDiagnosticEvents = diagnosticEvents.length > 0
  const stale = failed || Boolean(snapshot.stale)
  const canAssertClean = !stale
    && model.connection.state === 'online'
    && (model.freshness === 'live' || model.freshness === 'fresh')
  const observed = new Date(snapshot.observed_at)
  const date = Number.isNaN(observed.getTime()) ? 'Дата неизвестна' : new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' }).format(observed).replace(/\s*г\.$/, '')
  const time = Number.isNaN(observed.getTime()) ? '' : new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(observed)
  const retry = !online || failed
  const battery = (connected: boolean | null | undefined, percent: number | null) => connected === false
    ? 'Не подключена'
    : percent == null ? 'Нет данных' : `${percent} %`
  return <section className="rp-check-summary" aria-label="Состояние робота" aria-busy={pending}>
    <h2>Робот {model.shortNumber}</h2>
    <dl className="rp-check-summary-values">
      <div><dt>АКБ 1</dt><dd>{battery(snapshot.battery1_connected, snapshot.battery1_percent)}</dd></div>
      <div><dt>АКБ 2</dt><dd>{battery(snapshot.battery2_connected, snapshot.battery2_percent)}</dd></div>
      <div><dt>Скорость</dt><dd>{snapshot.speed == null ? 'Нет данных' : `${snapshot.speed} м/с`}</dd></div>
      <div><dt>Диск</dt><dd>{snapshot.disk_percent == null ? 'Нет данных' : `${snapshot.disk_percent} %`}</dd></div>
      <div><dt>Связь</dt><dd><StatusBadge tone={model.connection.tone}>{model.connection.label}</StatusBadge></dd></div>
    </dl>
    {snapshot.stale ? <StatusBadge tone="warning" className="rp-freshness-badge">Данные устарели · {Math.round(snapshot.stale_age_seconds ?? 0)} с</StatusBadge>
      : <StaleBadge state={failed ? 'stale' : model.freshness} updatedAt={snapshot.observed_at} />}
    <p><time dateTime={snapshot.observed_at}>Данные на {date}{time ? ` · ${time}` : ''}</time></p>
    {leading ? <div className="rp-check-leading-diagnostic">
      <strong>{leading.title}</strong>
      <p>{leading.description}</p>
      <Button variant="secondary" onClick={onShowDiagnostic}>Показать неисправность</Button>
    </div> : null}
    {model.criticalReason ? <p className="rp-check-critical" role="status">{model.criticalReason}</p> : null}
    {!leading && hasDiagnosticEvents && !model.criticalReason
      ? <p role="status">Обнаружены активные ошибки. Откройте раздел «Ошибки».</p>
      : null}
    {canAssertClean && !leading && !hasDiagnosticEvents && !model.criticalReason
      ? <p role="status">Активных ошибок нет</p>
      : null}
    <details className="rp-check-supplementary"><summary>VIN и координаты</summary>
      <p className="rp-check-vin">{snapshot.vin}</p>
      <p>{snapshot.lat != null && snapshot.lon != null ? `${snapshot.lat}, ${snapshot.lon}` : 'Координаты не получены'}</p>
      <RobotQrButton vin={snapshot.vin} />
    </details>
    {retry ? <Button onClick={onRefresh}>Повторить проверку</Button> : null}
  </section>
}
