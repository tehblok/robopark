import { createPortal } from 'react-dom'
import { ru } from '../../i18n/ru'
import { maintenanceKindLabel, shouldShowMaintenance, type MaintenanceStatus } from './maintenanceLogic'

export function MaintenanceOverlay({ status }: { status: MaintenanceStatus | null }) {
  if (!shouldShowMaintenance(status)) return null
  const detail = maintenanceKindLabel(
    status?.kind ?? null,
    {
      snapshot: ru.maintenance.snapshot,
      restore: ru.maintenance.restore,
      update: ru.maintenance.update,
    },
    ru.maintenance.body,
  )
  return createPortal(
    <div className="ops-maintenance" role="alertdialog" aria-live="polite" aria-labelledby="ops-maintenance-title">
      <div className="ops-maintenance-card">
        <div className="ops-maintenance-orbit" aria-hidden>
          <span />
          <span />
          <span />
        </div>
        <h1 id="ops-maintenance-title">{ru.maintenance.title}</h1>
        <p>{detail}</p>
        <p className="ops-maintenance-sub">{ru.maintenance.body}</p>
      </div>
    </div>,
    document.body,
  )
}
