import { useCallback, useContext, useEffect, useLayoutEffect, useState } from 'react'
import { api } from '../../api'
import { resourceStore, useCachedResource } from '../../lib/resource'
import { AuthContext } from '../../auth-context'
import { useParkScope } from '../../app/park/parkScope'
import { MetricCard } from '../../design-system/data/MetricCard'
import { EmptyState, LoadingState } from '../../design-system/feedback/AsyncState'
import { PageLayout } from '../../design-system/layout/PageLayout'
import { InventoryManageView } from './InventoryManageView'
import { InventoryPartsView } from './InventoryPartsView'
import { InventoryReceiptsView } from './InventoryReceiptsView'
import { InventoryCountsView } from './InventoryCountsView'
import { InventoryExportView } from './InventoryExportView'
import { InventoryTabs } from './InventoryTabs'
import { INVENTORY_REVISION_CHANGED } from './inventoryRevision'
import './inventory.css'

type InventoryApi = Pick<typeof api, 'inventory' | 'inventoryPartPhotoUrl' | 'searchInventory' | 'inventoryCatalogComponents' | 'getInventoryCatalogPart' | 'createInventoryCatalogComponent' | 'createInventoryCatalogPart' | 'updateInventoryCatalogPart' | 'mergeInventoryCatalogPart' | 'updateInventoryStock' | 'inventoryReceipts' | 'createInventoryReceipt' | 'updateInventoryReceipt' | 'postInventoryReceipt' | 'cancelInventoryReceipt' | 'reverseInventoryReceipt' | 'inventoryCounts' | 'createInventoryCount' | 'updateInventoryCount' | 'refreshInventoryCount' | 'postInventoryCount' | 'cancelInventoryCount' | 'downloadInventoryExport'>

export function InventoryPage({ apiClient = api }: { apiClient?: InventoryApi }) {
  const { selectedPark, parks, loading } = useParkScope()
  const user = useContext(AuthContext)?.user
  const role = user?.role
  const permissions = user?.permissions
  const readOnly = role === 'operator'
  const canManage = !readOnly && (permissions === undefined || permissions.includes('inventory.stock.manage'))
  const canPrint = !readOnly && (permissions === undefined || permissions.includes('inventory.export') || canManage)
  const [refreshVersion, setRefreshVersion] = useState(0)
  const parkId = selectedPark?.id
  const accessPrefix = `inventory-kpi:${JSON.stringify([user?.id ?? null, role ?? null, permissions?.slice().sort(), parks.map(park => park.id).sort((a, b) => a - b)])}:`
  const overviewKey = `${accessPrefix}${parkId ?? 'none'}`
  useLayoutEffect(() => {
    resourceStore.activateScope('inventory-kpi', accessPrefix)
    return () => resourceStore.cancelPending(overviewKey)
  }, [accessPrefix, overviewKey])
  const overviewResource = useCachedResource(overviewKey, async () => {
    const { park_id, component_count, part_count, low_stock_count, out_of_stock_count } = await apiClient.inventory(parkId!)
    // The KPI cache needs five numbers, not a second copy of the full catalog.
    return { key: overviewKey, value: { park_id, component_count, part_count, low_stock_count, out_of_stock_count } }
  }, { enabled: Boolean(parkId) && !loading, persist: false, trackProgress: false, staleTimeMs: 60_000, refreshIntervalMs: 0 })
  // A changed key must never paint the previous park/account while effects settle.
  const overview = overviewResource.data?.key === overviewKey && overviewResource.data.value.park_id === parkId ? overviewResource.data.value : null
  const refreshOverview = overviewResource.refresh
  const loadOverview = useCallback(() => {
    resourceStore.invalidate(overviewKey)
    void refreshOverview()
  }, [overviewKey, refreshOverview])
  useEffect(() => {
    const changed = (event: Event) => {
      if ((event as CustomEvent<number>).detail !== parkId) return
      loadOverview()
      setRefreshVersion(current => current + 1)
    }
    window.addEventListener(INVENTORY_REVISION_CHANGED, changed)
    return () => window.removeEventListener(INVENTORY_REVISION_CHANGED, changed)
  }, [parkId, loadOverview])
  if (loading) return <LoadingState label="Загружаем парк" variant="page" />
  if (!selectedPark) return <EmptyState description="Выберите парк." icon="parks" title="Парк не выбран" />

  return <PageLayout description={`Учёт запчастей парка «${selectedPark.name}»`} title="Склад">
    {overview ? <div className="stat-grid inventory-kpis"><MetricCard label="Компоненты" value={overview.component_count} /><MetricCard label="Запчасти" value={overview.part_count} /><MetricCard label="Ниже минимума" tone={overview.low_stock_count ? 'warning' : 'neutral'} value={overview.low_stock_count} /><MetricCard label="Нет на складе" tone={overview.out_of_stock_count ? 'critical' : 'neutral'} value={overview.out_of_stock_count} /></div> : null}
    <InventoryTabs readOnly={readOnly} renderPanel={view => view === 'parts'
      ? <InventoryPartsView apiClient={apiClient} canManage={canManage} canPrint={canPrint} parkId={selectedPark.id} refreshVersion={refreshVersion} />
      : view === 'receipts'
        ? <InventoryReceiptsView apiClient={apiClient} onInventoryChanged={loadOverview} parkId={selectedPark.id} permissions={permissions} refreshVersion={refreshVersion} />
        : view === 'counts'
          ? <InventoryCountsView apiClient={apiClient} onInventoryChanged={loadOverview} parkId={selectedPark.id} permissions={permissions} refreshVersion={refreshVersion} />
      : view === 'manage'
        ? <InventoryManageView apiClient={apiClient} parkId={selectedPark.id} role={role} refreshVersion={refreshVersion} />
        : <InventoryExportView apiClient={apiClient} parks={parks} permissions={permissions} role={role} selectedPark={selectedPark} />} />
  </PageLayout>
}
