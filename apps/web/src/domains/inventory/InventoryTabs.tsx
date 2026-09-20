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

export function InventoryTabs({ renderPanel, readOnly = false }: { renderPanel: (view: InventoryView) => ReactNode; readOnly?: boolean }) {
  const [searchParams, setSearchParams] = useSearchParams()
  const rawView = searchParams.get('view')
  const visibleItems = readOnly ? tabItems.filter(item => item.id === 'parts') : tabItems
  const activeView: InventoryView = !readOnly && isInventoryView(rawView) ? rawView : 'parts'

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

  return (
    <section className="inventory-workflows">
      <select
        aria-label="Раздел склада"
        className="inventory-workflows__mobile-select"
        onChange={(event) => setView(event.target.value)}
        value={activeView}
      >
        {visibleItems.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
      </select>
      <Tabs
        ariaLabel="Разделы склада"
        items={visibleItems}
        onChange={setView}
        panelIdFor={view => `inventory-panel-${view}`}
        value={activeView}
      />
      {visibleItems.map(item => {
        const active = item.id === activeView
        return <TabPanel active={active} id={`inventory-panel-${item.id}`} key={item.id} labelledBy={`tab-${item.id}`}>
          {active ? <div data-inventory-workflow={item.id}>{renderPanel(item.id)}</div> : null}
        </TabPanel>
      })}
    </section>
  )
}
