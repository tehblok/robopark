import { useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { StaleBadge } from '../../design-system/feedback/AsyncState'
import { Icon } from '../../design-system/icons/Icon'
import { Panel } from '../../design-system/layout/PageLayout'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import type { RobotDetailViewModel } from './robotDetailModel'
import { ROBOT_PHOTOS } from './robotPhotos'
import { RobotQrButton } from './RobotQrButton'

export function RobotIdentityCard({ model, canOpenCheck, parkId, children }: {
  model: RobotDetailViewModel
  canOpenCheck: boolean
  parkId: number | null
  children?: ReactNode
}) {
  const [imageFailed, setImageFailed] = useState(false)
  const photo = ROBOT_PHOTOS.find((item) => item.id === 'isometric')!
  const search = parkId == null ? '' : `?park=${parkId}`
  return (
    <>
      <div className="rp-robot-detail__identity">
        <Panel className="rp-robot-identity" title={`Робот ${model.shortNumber}`}>
          <figure className="rp-robot-identity__illustration">
            {imageFailed ? <span aria-label="Схема модели робота" role="img"><Icon name="robot" size={80} /></span> : (
              <img alt="Иллюстрация модели робота" src={photo.src} width={photo.width} height={photo.height} loading="lazy" decoding="async" onError={() => setImageFailed(true)} />
            )}
            <figcaption>Иллюстрация модели</figcaption>
          </figure>
          <p className="rp-robot-identity__vin">{model.vin}</p>
          <RobotQrButton vin={model.vin} />
          <div className="rp-robot-identity__status">
            <StatusBadge tone={model.connection.tone}>{model.connection.label}</StatusBadge>
            <StaleBadge state={model.freshness} updatedAt={model.observedAt} />
            {model.criticalReason ? <StatusBadge tone="critical">{model.criticalReason}</StatusBadge> : null}
          </div>
        </Panel>
        {children}
      </div>
      {canOpenCheck ? (
        <div className="rp-robot-detail__primary">
          <Link className="rp-button rp-button--primary rp-button--comfortable" to={`/robots/${encodeURIComponent(model.vin)}/check${search}`}>
            Начать проверку робота
          </Link>
        </div>
      ) : null}
    </>
  )
}
