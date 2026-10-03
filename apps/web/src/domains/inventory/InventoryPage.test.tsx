import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ApiError, api, type InventoryOverview, type Park } from '../../api'
import { AuthContext } from '../../auth-context'
import { ParkScopeContext } from '../../app/park/parkScope'
import { InventoryPage } from './InventoryPage'
import { TaskPartsPanel } from './TaskPartsPanel'
import { INVENTORY_REVISION_CHANGED } from './inventoryRevision'
import { resourceStore } from '../../lib/resource'

const park: Park = { id: 7, name: 'Север', timezone: 'Europe/Moscow', tag: 'North', is_active: true }
const stock: InventoryOverview = { park_id: 7, component_count: 1, part_count: 1, low_stock_count: 0, out_of_stock_count: 0, components: [{ id: 2, park_id: 7, name: 'Подвязка', has_photo: true, parts: [{ id: 3, park_id: 7, component_id: 2, name: 'Тяга', article: 'TY-001', quantity: '5', minimum_quantity: '2', location: 'Стеллаж A / полка 2', is_active: true, has_photo: true }] }] }


function inventoryClient(overrides = {}) {
  return { ...api, inventory: vi.fn(async () => stock), ...overrides }
}

function LocationProbe() {
  const location = useLocation()
  return <output aria-label="Адрес">{location.pathname}{location.search}</output>
}

function renderInventoryPage(currentPark: Park, client = inventoryClient(), path = '/inventory?view=parts') {
  return <MemoryRouter initialEntries={[path]}><ParkScopeContext.Provider value={{ parkId: currentPark.id, selectedPark: currentPark, parks: [currentPark], loading: false, locked: true, setParkId: vi.fn(), refreshParks: vi.fn() }}><InventoryPage apiClient={client} /><LocationProbe /></ParkScopeContext.Provider></MemoryRouter>
}

function useViewport(matches: boolean) {
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({
    matches,
    media: '(max-width: 599px)',
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }))
}

beforeEach(() => { resourceStore.clearAll(); useViewport(false) })

afterEach(() => { resourceStore.clearAll(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('reuses completed KPI data on navigation without persisting protected inventory', async () => {
  const client = inventoryClient({
    searchInventory: vi.fn(async () => ({ items: [], limit: 25, offset: 0, total: 0 })),
    inventoryCatalogComponents: vi.fn(async () => ({ items: [], limit: 200, offset: 0, total: 0 })),
  })
  const view = render(renderInventoryPage(park, client))
  await screen.findByText('Компоненты')
  view.unmount()
  render(renderInventoryPage(park, client))
  await screen.findByText('Компоненты')
  expect(client.inventory).toHaveBeenCalledTimes(1)
  expect(Object.values(localStorage).join(' ')).not.toContain('TY-001')
})

it('does not publish an old park KPI response after switching parks', async () => {
  let finishOld!: (value: InventoryOverview) => void
  const client = inventoryClient({
    inventory: vi.fn((id: number) => id === 7 ? new Promise<InventoryOverview>(resolve => { finishOld = resolve }) : Promise.resolve({ ...stock, park_id: 8, part_count: 88 })),
    searchInventory: vi.fn(async () => ({ items: [], limit: 25, offset: 0, total: 0 })),
    inventoryCatalogComponents: vi.fn(async () => ({ items: [], limit: 200, offset: 0, total: 0 })),
  })
  const view = render(renderInventoryPage(park, client))
  await waitFor(() => expect(client.inventory).toHaveBeenCalledWith(7))
  view.rerender(renderInventoryPage({ ...park, id: 8, name: 'Юг' }, client))
  await screen.findByText('88')
  finishOld({ ...stock, part_count: 777 })
  await waitFor(() => expect(screen.queryByText('777')).not.toBeInTheDocument())
  expect(screen.getByText('88')).toBeVisible()
})

it('keeps operator inventory read-only even with a stale permissive client permission list', async () => {
  const user = { id: 2, username: 'operator', role: 'operator', access_status: 'approved', parks: [park], permissions: ['inventory.stock.manage', 'inventory.export'] }
  const client = inventoryClient({
    searchInventory: vi.fn(async () => ({ items: [{ id: 3, name: 'Тяга', article: 'TY-001', component_id: 2, component_name: 'Подвязка', quantity: '5', minimum_quantity: '2', location: 'Полка 2', has_photo: false, is_active: true, stock_is_active: true }], limit: 25, offset: 0, total: 1 })),
    inventoryCatalogComponents: vi.fn(async () => ({ items: [], limit: 200, offset: 0, total: 0 })),
  })
  render(<AuthContext.Provider value={{ user, loading: false, login: vi.fn(), refreshUser: vi.fn(), logout: vi.fn() }}>{renderInventoryPage(park, client, '/inventory?view=manage')}</AuthContext.Provider>)
  expect(await screen.findByText('Полка 2')).toBeVisible()
  expect(screen.queryByRole('tab', { name: 'Управление' })).not.toBeInTheDocument()
  expect(screen.queryByRole('tab', { name: 'Выгрузка' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Настроить остаток' })).not.toBeInTheDocument()
  expect(screen.queryByRole('group', { name: 'Печать этикеток' })).not.toBeInTheDocument()
})

it('refreshes only the active park inventory without clearing the search', async () => {
  const client = inventoryClient({
    searchInventory: vi.fn(async () => ({ items: [], limit: 25, offset: 0, total: 0 })),
    inventoryCatalogComponents: vi.fn(async () => ({ items: [], limit: 200, offset: 0, total: 0 })),
  })
  render(renderInventoryPage(park, client))
  const search = await screen.findByRole('searchbox', { name: 'Найти запчасть' })
  await userEvent.type(search, 'тяга')
  await waitFor(() => expect(client.searchInventory).toHaveBeenCalledWith(expect.objectContaining({ query: 'тяга' })))
  const before = vi.mocked(client.searchInventory).mock.calls.length
  window.dispatchEvent(new CustomEvent(INVENTORY_REVISION_CHANGED, { detail: 9 }))
  expect(vi.mocked(client.searchInventory).mock.calls.length).toBe(before)
  window.dispatchEvent(new CustomEvent(INVENTORY_REVISION_CHANGED, { detail: 7 }))
  await waitFor(() => expect(vi.mocked(client.searchInventory).mock.calls.length).toBeGreaterThan(before))
  expect(search).toHaveValue('тяга')
})

it('mounts the fast parts and scoped manage views through the active shell panel', async () => {
  const client = inventoryClient({
    searchInventory: vi.fn(async () => ({ items: [], limit: 25, offset: 0, total: 0 })),
    inventoryCatalogComponents: vi.fn(async () => ({ items: [], limit: 200, offset: 0, total: 0 })),
  })
  const user = { id: 1, username: 'mech', role: 'mechanic', access_status: 'approved', parks: [park] }
  const view = render(<MemoryRouter initialEntries={['/inventory?view=parts']}><AuthContext.Provider value={{ user, loading: false, login: vi.fn(), refreshUser: vi.fn(), logout: vi.fn() }}><ParkScopeContext.Provider value={{ parkId: 7, selectedPark: park, parks: [park], loading: false, locked: true, setParkId: vi.fn(), refreshParks: vi.fn() }}><InventoryPage apiClient={client} /></ParkScopeContext.Provider></AuthContext.Provider></MemoryRouter>)

  expect(await screen.findByRole('searchbox', { name: 'Найти запчасть' })).toBeVisible()
  expect(document.querySelectorAll('[data-inventory-workflow]')).toHaveLength(1)
  view.unmount()

  render(<MemoryRouter initialEntries={['/inventory?view=manage']}><AuthContext.Provider value={{ user, loading: false, login: vi.fn(), refreshUser: vi.fn(), logout: vi.fn() }}><ParkScopeContext.Provider value={{ parkId: 7, selectedPark: park, parks: [park], loading: false, locked: true, setParkId: vi.fn(), refreshParks: vi.fn() }}><InventoryPage apiClient={client} /></ParkScopeContext.Provider></AuthContext.Provider></MemoryRouter>)
  expect(await screen.findByRole('button', { name: 'Добавить позицию' })).toBeVisible()
  expect(document.querySelectorAll('[data-inventory-workflow]')).toHaveLength(1)
})

it.each([
  ['/inventory?view=receipts', 'Новая поставка'],
  ['/inventory?view=counts', 'Новая инвентаризация'],
] as const)('mounts the active inventory document workflow at %s', async (path, action) => {
  const client = inventoryClient({
    inventoryReceipts: vi.fn(async () => ({ items: [], limit: 25, offset: 0, total: 0 })),
    inventoryCounts: vi.fn(async () => ({ items: [], limit: 25, offset: 0, total: 0 })),
    searchInventory: vi.fn(async () => ({ items: [], limit: 25, offset: 0, total: 0 })),
  })
  render(renderInventoryPage(park, client, path))
  expect(await screen.findByRole('button', { name: action })).toBeVisible()
  expect(document.querySelectorAll('[data-inventory-workflow]')).toHaveLength(1)
})

it('keeps park identity and KPI strip above export without coupling the workflow to overview success', async () => {
  const client = inventoryClient()
  render(renderInventoryPage(park, client, '/inventory?park=7&view=export'))

  expect(await screen.findByRole('heading', { name: 'Склад' })).toBeVisible()
  expect(screen.getByText('Учёт запчастей парка «Север»')).toBeVisible()
  expect(await screen.findByText('Компоненты')).toBeVisible()
  expect(screen.getByRole('tabpanel')).toHaveTextContent('Выгрузка парка Север')
  const metrics = document.querySelector('.inventory-kpis')!
  const tabs = screen.getByRole('tablist', { name: 'Разделы склада' })
  expect(metrics.compareDocumentPosition(tabs) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
})

it('keeps tabs and export usable when the optional overview KPI request fails', async () => {
  const client = inventoryClient({ inventory: vi.fn(async () => { throw new Error('legacy unavailable') }) })
  render(renderInventoryPage(park, client, '/inventory?park=7&view=export'))

  expect(await screen.findByRole('tabpanel')).toHaveTextContent('Выгрузка парка Север')
  expect(screen.getByRole('tablist', { name: 'Разделы склада' })).toBeVisible()
  expect(client.inventory).toHaveBeenCalledWith(7)
})

it('shows the failed KPI request and retries it without blocking the active tab', async () => {
  const inventory = vi.fn().mockRejectedValueOnce(new ApiError(503, 'host_unavailable'))
    .mockResolvedValueOnce(stock)
  const client = inventoryClient({ inventory })
  render(renderInventoryPage(park, client, '/inventory?park=7&view=export'))

  const metrics = await screen.findByRole('region', { name: 'Показатели склада' })
  expect(await within(metrics).findByRole('alert')).toHaveTextContent('Сервис временно недоступен')
  expect(screen.getByRole('tabpanel')).toHaveTextContent('Выгрузка парка Север')
  await userEvent.click(within(metrics).getByRole('button', { name: 'Повторить' }))
  expect(await screen.findByText('Компоненты')).toBeVisible()
  await waitFor(() => expect(within(metrics).queryByRole('alert')).not.toBeInTheDocument())
  expect(inventory).toHaveBeenCalledTimes(2)
})

it('removes protected inventory content after a later overview access denial', async () => {
  const inventory = vi.fn().mockResolvedValueOnce(stock)
    .mockRejectedValueOnce(new ApiError(403, 'forbidden'))
  const client = inventoryClient({
    inventory,
    searchInventory: vi.fn(async () => ({ items: [{ id: 3, name: 'Тяга', article: 'TY-001', component_id: 2, component_name: 'Подвязка', quantity: '5', minimum_quantity: '2', location: 'Полка 2', has_photo: false, is_active: true, stock_is_active: true }], limit: 25, offset: 0, total: 1 })),
    inventoryCatalogComponents: vi.fn(async () => ({ items: [], limit: 200, offset: 0, total: 0 })),
  })
  render(renderInventoryPage(park, client, '/inventory?park=7&view=parts'))
  expect(await screen.findByText('Компоненты')).toBeVisible()
  expect(await screen.findByText('Полка 2')).toBeVisible()

  window.dispatchEvent(new CustomEvent(INVENTORY_REVISION_CHANGED, { detail: 7 }))

  const metrics = await screen.findByRole('region', { name: 'Показатели склада' })
  expect(await within(metrics).findByRole('alert')).toHaveTextContent('Нет доступа')
  expect(screen.queryByText('Компоненты')).not.toBeInTheDocument()
  expect(screen.queryByText('Полка 2')).not.toBeInTheDocument()
  expect(screen.queryByRole('tablist', { name: 'Разделы склада' })).not.toBeInTheDocument()
})

it('labels retained KPIs as stale after a failed refresh', async () => {
  const inventory = vi.fn().mockResolvedValueOnce(stock)
    .mockRejectedValueOnce(new ApiError(503, 'host_unavailable'))
  render(renderInventoryPage(park, inventoryClient({ inventory }), '/inventory?park=7&view=export'))
  const metrics = await screen.findByRole('region', { name: 'Показатели склада' })
  expect(await within(metrics).findByText('Компоненты')).toBeVisible()

  window.dispatchEvent(new CustomEvent(INVENTORY_REVISION_CHANGED, { detail: 7 }))

  expect(await within(metrics).findByRole('alert')).toHaveTextContent('Показатели могут устареть')
  expect(within(metrics).getByText('Компоненты')).toBeVisible()
  expect(inventory).toHaveBeenCalledTimes(2)
})

it('writes a selected part off from the current task without rounding int64 input', async () => {
  const largeStock: InventoryOverview = { ...stock, components: [{ ...stock.components[0], parts: [{ ...stock.components[0].parts[0], quantity: '9223372036854775807' }] }] }
  const writeoff = vi.fn(async () => ({ id: 1, part_id: 3, park_id: 7, actor_user_id: 4, actor_username: 'mech', kind: 'task_writeoff', delta: '-2' as const, balance_after: '3' as const, issue_key: 'RP-42', note: null, created_at: '2026-09-10T10:00:00Z' }))
  render(<TaskPartsPanel apiClient={inventoryClient({ inventory: vi.fn(async () => largeStock), writeoffInventoryForTask: writeoff })} issueKey="RP-42" parkId={7} />)
  await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Компонента' }), '2')
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Запчасть' }), '3')
  await userEvent.clear(screen.getByRole('textbox', { name: 'Списать, шт.' }))
  await userEvent.type(screen.getByRole('textbox', { name: 'Списать, шт.' }), '9007199254740993')
  await userEvent.click(screen.getByRole('button', { name: 'Списать в задачу' }))
  await waitFor(() => expect(writeoff).toHaveBeenCalledWith('RP-42', 3, '9007199254740993', expect.any(String)))
})

it('resets an invalid write-off quantity after inventory refresh', async () => {
  const refreshedStock: InventoryOverview = { ...stock, components: [{ ...stock.components[0], parts: [{ ...stock.components[0].parts[0], quantity: '1' }] }] }
  const client = inventoryClient({ inventory: vi.fn().mockResolvedValueOnce(stock).mockResolvedValueOnce(refreshedStock), writeoffInventoryForTask: vi.fn(async () => ({ id: 1, part_id: 3, park_id: 7, actor_user_id: 4, actor_username: 'mech', kind: 'task_writeoff', delta: '-2' as const, balance_after: '1' as const, issue_key: 'RP-42', note: null, created_at: '2026-09-10T10:00:00Z' })) })
  render(<TaskPartsPanel apiClient={client} issueKey="RP-42" parkId={7} />)

  await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Компонента' }), '2')
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Запчасть' }), '3')
  await userEvent.clear(screen.getByRole('textbox', { name: 'Списать, шт.' }))
  await userEvent.type(screen.getByRole('textbox', { name: 'Списать, шт.' }), '2')
  await userEvent.click(screen.getByRole('button', { name: 'Списать в задачу' }))

  await waitFor(() => expect(client.inventory).toHaveBeenCalledTimes(2))
  expect(screen.getByRole('button', { name: 'Списать в задачу' })).toBeDisabled()
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Запчасть' }), '3')
  expect(screen.getByRole('textbox', { name: 'Списать, шт.' })).toHaveValue('1')
})
