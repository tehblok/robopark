import { api } from '../../api'
import { useCachedResource } from '../../lib/resource'
import { MaintenanceOverlay } from './MaintenanceOverlay'

export function MaintenanceGate() {
  // Shared, non-persisted state retains the last successful maintenance lock
  // through transient failures and stops background work offline/hidden.
  const status = useCachedResource('ops:maintenance', () => api.opsMaintenance(), {
    persist: false,
    trackProgress: false,
  })
  return <MaintenanceOverlay status={status.data ?? null} />
}
