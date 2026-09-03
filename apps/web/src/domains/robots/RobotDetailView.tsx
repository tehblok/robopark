import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import type { Blocker, EmergencySnapshot } from '../../api'
import { Button } from '../../design-system/actions/Button'
import { EmptyState, ErrorState, LoadingState } from '../../design-system/feedback/AsyncState'
import { Panel } from '../../design-system/layout/PageLayout'
import type { DomainError } from '../../shared/api/classifyApiError'
import { RobotIdentityCard } from './RobotIdentityCard'
import { buildRobotDetailModel } from './robotDetailModel'
import './robots.css'

export type RobotDetailViewProps = {
  snapshot: EmergencySnapshot
  relatedWork: Blocker[] | null
  relatedWorkError: DomainError | null
  relatedWorkLoading?: boolean
  snapshotError?: DomainError | null
  workScopeLabel: string
  parkId: number | null
  browserOnline: boolean
  canOpenCheck: boolean
  canOpenWork: boolean
  onRetrySnapshot: () => void
  onRetryWork: () => void
}

export function RobotDetailView({ snapshot, relatedWork, relatedWorkError, relatedWorkLoading = false, snapshotError, workScopeLabel, parkId, browserOnline, canOpenCheck, canOpenWork, onRetrySnapshot, onRetryWork }: RobotDetailViewProps) {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const clock = globalThis.setInterval(() => setNow(new Date()), 30_000)
    return () => globalThis.clearInterval(clock)
  }, [])
  const model = buildRobotDetailModel(snapshot, browserOnline, now)
  if (snapshotError) model.freshness = snapshotError.kind === 'offline' ? 'offline' : 'stale'
  const search = parkId == null ? '' : `?park=${parkId}`
  return (
    <div className="rp-robot-detail">
      <RobotIdentityCard model={model} canOpenCheck={canOpenCheck} parkId={parkId}>
        {snapshotError ? <ErrorState {...snapshotError} onRetry={snapshotError.retryable ? onRetrySnapshot : undefined} /> : null}
        <Button variant="secondary" leadingIcon="refresh" onClick={onRetrySnapshot}>Обновить данные робота</Button>
      </RobotIdentityCard>
      <Panel className="rp-robot-detail__work" title="Связанные задачи">
        <p>Область связанных задач: {workScopeLabel}</p>
        {parkId != null ? <p>Выбранный парк: {parkId}; он применяется к переходам в Работу</p> : null}
        {relatedWorkError ? <ErrorState {...relatedWorkError} onRetry={relatedWorkError.retryable ? onRetryWork : undefined} />
          : relatedWork === null ? <p>Связанные задачи недоступны: нет разрешения на чтение Tracker.</p>
            : relatedWorkLoading ? <LoadingState label="Загружаем связанные задачи" />
              : relatedWork.length ? (
                <ul className="rp-robot-detail__tasks">
                  {relatedWork.map((item) => <li key={item.key}>
                    {canOpenWork ? <Link to={`/work/${encodeURIComponent(item.key)}${search}`} aria-label={`Открыть ${item.key}`}>{item.key}</Link> : <strong>{item.key}</strong>}
                    <p>{item.summary}</p><span>{item.status}</span>
                  </li>)}
                </ul>
              ) : <p>Связанных задач нет в доступной области.</p>}
      </Panel>
      <div className="rp-robot-detail__events"><EmptyState title="История событий пока недоступна" description="Источник истории событий пока не подключён." icon="info" /></div>
    </div>
  )
}
