import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, type InventoryOverview, type Park } from '../../api'
import { AuthContext } from '../../auth-context'
import { ParkScopeContext } from '../../app/park/parkScope'
import { InventoryPage } from './InventoryPage'
import { TaskPartsPanel } from './TaskPartsPanel'

const park: Park = { id: 7, name: 'Север', tag: 'North', is_active: true }
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

beforeEach(() => useViewport(false))

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

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

it('writes a selected part off from the current task without rounding int64 input', async () => {
  const largeStock: InventoryOverview = { ...stock, components: [{ ...stock.components[0], parts: [{ ...stock.components[0].parts[0], quantity: '9223372036854775807' }] }] }
  const writeoff = vi.fn(async () => ({ id: 1, part_id: 3, park_id: 7, actor_user_id: 4, actor_username: 'mech', kind: 'task_writeoff', delta: '-2' as const, balance_after: '3' as const, issue_key: 'RP-42', note: null, created_at: '2026-09-10T10:00:00Z' }))
  render(<TaskPartsPanel apiClient={inventoryClient({ inventory: vi.fn(async () => largeStock), writeoffInventoryForTask: writeoff })} issueKey="RP-42" parkId={7} />)
  await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Компонента' }), '2')
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Запчасть' }), '3')
  await userEvent.clear(screen.getByRole('textbox', { name: 'Списать, шт.' }))
  await userEvent.type(screen.getByRole('textbox', { name: 'Списать, шт.' }), '9007199254740993')
  await userEvent.click(screen.getByRole('button', { name: 'Списать в задачу' }))
  await waitFor(() => expect(writeoff).toHaveBeenCalledWith('RP-42', 3, '9007199254740993'))
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
  expect(screen.getByRole('textbox', { name: 'Списать, шт.' })).toHaveValue('1')
})
