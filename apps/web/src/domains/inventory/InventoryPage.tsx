import { useCallback, useContext, useLayoutEffect, useRef, useState } from 'react'
import { api, type InventoryOverview } from '../../api'
import { AuthContext } from '../../auth-context'
import { useParkScope } from '../../app/park/parkScope'
import { MetricCard } from '../../design-system/data/MetricCard'
import { EmptyState, LoadingState } from '../../design-system/feedback/AsyncState'
import { PageLayout } from '../../design-system/layout/PageLayout'
import { InventoryManageView } from './InventoryManageView'
import { InventoryPartsView } from './InventoryPartsView'
import { InventoryTabs, type InventoryView } from './InventoryTabs'
import './inventory.css'

type InventoryApi = Pick<typeof api, 'inventory' | 'inventoryPartPhotoUrl' | 'searchInventory' | 'inventoryCatalogComponents' | 'getInventoryCatalogPart' | 'createInventoryCatalogComponent' | 'createInventoryCatalogPart' | 'updateInventoryCatalogPart' | 'mergeInventoryCatalogPart' | 'updateInventoryStock'>

function InventoryWorkflowPlaceholder({ view, parkName, role }: { view: Exclude<InventoryView, 'parts'>; parkName: string; role?: string }) {
  if (view === 'receipts') return <section className="inventory-workflow-placeholder"><h2>Поставки</h2><p>Список и редактор поставок появятся здесь.</p></section>
  if (view === 'counts') return <section className="inventory-workflow-placeholder"><h2>Инвентаризация</h2><p>Акты и фактические остатки появятся здесь.</p></section>
  if (view === 'manage') return <section className="inventory-workflow-placeholder"><h2>Управление</h2><p>{role === 'admin' || role === 'royal' ? 'Глобальный каталог и настройки склада парка.' : 'Настройки склада парка.'}</p></section>
  return <section className="inventory-workflow-placeholder"><h2>Выгрузка парка {parkName}</h2><p>Выбор формата и скачивание появятся здесь.</p></section>
}

export function InventoryPage({ apiClient = api }: { apiClient?: InventoryApi }) {
  const { selectedPark, loading } = useParkScope()
  const role = useContext(AuthContext)?.user?.role
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
      : view === 'manage'
        ? <InventoryManageView apiClient={apiClient} parkId={selectedPark.id} role={role} />
        : <InventoryWorkflowPlaceholder parkName={selectedPark.name} role={role} view={view} />} />
  </PageLayout>
}
