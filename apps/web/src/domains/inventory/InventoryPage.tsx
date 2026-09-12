import { useCallback, useContext, useLayoutEffect, useRef, useState } from 'react'
import { api, type InventoryOverview } from '../../api'
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
import './inventory.css'

type InventoryApi = Pick<typeof api, 'inventory' | 'inventoryPartPhotoUrl' | 'searchInventory' | 'inventoryCatalogComponents' | 'getInventoryCatalogPart' | 'createInventoryCatalogComponent' | 'createInventoryCatalogPart' | 'updateInventoryCatalogPart' | 'mergeInventoryCatalogPart' | 'updateInventoryStock' | 'inventoryReceipts' | 'createInventoryReceipt' | 'updateInventoryReceipt' | 'postInventoryReceipt' | 'cancelInventoryReceipt' | 'reverseInventoryReceipt' | 'inventoryCounts' | 'createInventoryCount' | 'updateInventoryCount' | 'refreshInventoryCount' | 'postInventoryCount' | 'cancelInventoryCount' | 'downloadInventoryExport'>

export function InventoryPage({ apiClient = api }: { apiClient?: InventoryApi }) {
  const { selectedPark, parks, loading } = useParkScope()
  const role = useContext(AuthContext)?.user?.role
  const permissions = useContext(AuthContext)?.user?.permissions
  const [overview, setOverview] = useState<InventoryOverview | null>(null)
  const requestGeneration = useRef(0)
  const parkId = selectedPark?.id
  const loadOverview = useCallback(() => {
    if (!parkId) return
    const generation = ++requestGeneration.current
    apiClient.inventory(parkId).then(value => {
      if (generation === requestGeneration.current) setOverview(value)
    }).catch(() => { /* Optional KPI data never blocks workflow navigation. */ })
  }, [apiClient, parkId])
  useLayoutEffect(() => {
    requestGeneration.current += 1
    setOverview(null)
    loadOverview()
    return () => { requestGeneration.current += 1 }
  }, [loadOverview])
  if (loading) return <LoadingState label="Загружаем парк" variant="page" />
  if (!selectedPark) return <EmptyState description="Выберите парк." icon="parks" title="Парк не выбран" />

  return <PageLayout description={`Учёт запчастей парка «${selectedPark.name}»`} title="Склад">
    {overview ? <div className="stat-grid inventory-kpis"><MetricCard label="Компоненты" value={overview.component_count} /><MetricCard label="Запчасти" value={overview.part_count} /><MetricCard label="Ниже минимума" tone={overview.low_stock_count ? 'warning' : 'neutral'} value={overview.low_stock_count} /><MetricCard label="Нет на складе" tone={overview.out_of_stock_count ? 'critical' : 'neutral'} value={overview.out_of_stock_count} /></div> : null}
    <InventoryTabs renderPanel={view => view === 'parts'
      ? <InventoryPartsView apiClient={apiClient} parkId={selectedPark.id} />
      : view === 'receipts'
        ? <InventoryReceiptsView apiClient={apiClient} onInventoryChanged={loadOverview} parkId={selectedPark.id} permissions={permissions} />
        : view === 'counts'
          ? <InventoryCountsView apiClient={apiClient} onInventoryChanged={loadOverview} parkId={selectedPark.id} permissions={permissions} />
      : view === 'manage'
        ? <InventoryManageView apiClient={apiClient} parkId={selectedPark.id} role={role} />
        : <InventoryExportView apiClient={apiClient} parks={parks} permissions={permissions} role={role} selectedPark={selectedPark} />} />
  </PageLayout>
}
