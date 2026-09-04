import type { EmergencySnapshot } from '../../api'
import { Button } from '../../design-system/actions/Button'
import { StaleBadge } from '../../design-system/feedback/AsyncState'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { buildRobotDetailModel } from './robotDetailModel'

export function RobotCheckSummary({ snapshot, online, failed, pending, onRefresh }: { snapshot: EmergencySnapshot; online: boolean; failed: boolean; pending: boolean; onRefresh: () => void }) {
  const model = buildRobotDetailModel(snapshot, online)
  const observed = new Date(snapshot.observed_at)
  const date = Number.isNaN(observed.getTime()) ? 'Дата неизвестна' : new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' }).format(observed).replace(/\s*г\.$/, '')
  const time = Number.isNaN(observed.getTime()) ? '' : new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(observed)
  const retry = !online || snapshot.online === false || Boolean(model.criticalReason)
  return <section className="rp-check-summary" aria-label="Состояние робота" aria-busy={pending}>
    <h2>Робот {model.shortNumber}</h2><p className="rp-check-vin">{snapshot.vin}</p>
    <StatusBadge tone={model.connection.tone}>{model.connection.label}</StatusBadge>
    <StaleBadge state={failed ? 'stale' : model.freshness} updatedAt={snapshot.observed_at} />
    <p><time dateTime={snapshot.observed_at}>Данные на {date}{time ? ` · ${time}` : ''}</time></p>
    <p>{snapshot.lat != null && snapshot.lon != null ? `${snapshot.lat}, ${snapshot.lon}` : 'Координаты не получены'}</p>
    {model.criticalReason ? <p className="rp-check-critical" role="status">{model.criticalReason}</p> : null}
    <Button leadingIcon="refresh" onClick={onRefresh}>{retry ? 'Повторить проверку' : 'Обновить данные'}</Button>
  </section>
}
