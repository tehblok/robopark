import { useEffect, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { TabPanel, Tabs } from '../../design-system/navigation/Tabs'

const inventoryViews = ['parts', 'receipts', 'counts', 'manage', 'export'] as const
export type InventoryView = typeof inventoryViews[number]

const tabItems: ReadonlyArray<{ id: InventoryView; label: string }> = [
  { id: 'parts', label: 'Запчасти' },
  { id: 'receipts', label: 'Поставки' },
  { id: 'counts', label: 'Инвентаризация' },
  { id: 'manage', label: 'Управление' },
  { id: 'export', label: 'Выгрузка' },
]

function isInventoryView(value: string | null): value is InventoryView {
  return inventoryViews.some(view => view === value)
}

export function InventoryTabs({ renderPanel }: { renderPanel: (view: InventoryView) => ReactNode }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const rawView = searchParams.get('view')
  const activeView: InventoryView = isInventoryView(rawView) ? rawView : 'parts'

  const setView = (view: string, replace = false) => {
    if (!isInventoryView(view)) return
    const next = new URLSearchParams(searchParams)
    next.set('view', view)
    setSearchParams(next, { replace })
  }

  useEffect(() => {
    if (rawView === activeView) return
    const next = new URLSearchParams(searchParams)
    next.set('view', activeView)
    setSearchParams(next, { replace: true })
  }, [activeView, rawView, searchParams, setSearchParams])

  const panelId = `inventory-panel-${activeView}`
  return (
    <section className="inventory-workflows">
      <Tabs
        ariaLabel="Разделы склада"
        items={tabItems}
        onChange={setView}
        panelIdFor={view => `inventory-panel-${view}`}
        value={activeView}
      />
      <TabPanel active id={panelId} labelledBy={`tab-${activeView}`}>
        {renderPanel(activeView)}
      </TabPanel>
    </section>
  )
}
