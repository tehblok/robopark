import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, it, vi } from 'vitest'
import { api, type InventoryOverview, type Park } from '../../api'
import { ParkScopeContext } from '../../app/park/parkScope'
import { InventoryPage } from './InventoryPage'
import { TaskPartsPanel } from './TaskPartsPanel'

const park: Park = { id: 7, name: 'Север', tag: 'North', is_active: true }
const stock: InventoryOverview = { park_id: 7, component_count: 1, part_count: 1, low_stock_count: 0, out_of_stock_count: 0, components: [{ id: 2, park_id: 7, name: 'Подвязка', has_photo: true, parts: [{ id: 3, park_id: 7, component_id: 2, name: 'Тяга', article: 'TY-001', quantity: 5, minimum_quantity: 2, location: 'Стеллаж A / полка 2', is_active: true, has_photo: true }] }] }
const stockWithTwoComponents: InventoryOverview = { ...stock, component_count: 2, part_count: 2, components: [...stock.components, { id: 9, park_id: 7, name: 'Колесо', has_photo: false, parts: [{ id: 10, park_id: 7, component_id: 9, name: 'Шина', article: 'WH-001', quantity: 4, minimum_quantity: 1, location: 'Стеллаж B / полка 1', is_active: true, has_photo: true }] }] }

function inventoryClient(overrides = {}) {
  return { ...api, inventory: vi.fn(async () => stock), ...overrides }
}

it('filters the compact catalog by component without another inventory request', async () => {
  const client = inventoryClient({ inventory: vi.fn(async () => stockWithTwoComponents) })
  render(<MemoryRouter><ParkScopeContext.Provider value={{ parkId: 7, selectedPark: park, parks: [park], loading: false, locked: true, setParkId: vi.fn(), refreshParks: vi.fn() }}><InventoryPage apiClient={client} /></ParkScopeContext.Provider></MemoryRouter>)

  expect(await screen.findByRole('heading', { name: 'Подвязка' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Колесо' })).toBeInTheDocument()
  expect(document.querySelectorAll('img.inventory-part__photo')).toHaveLength(2)

  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Компонента' }), '9')

  expect(screen.queryByRole('heading', { name: 'Подвязка' })).not.toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Колесо' })).toBeInTheDocument()
  expect(client.inventory).toHaveBeenCalledTimes(1)
})

it('prints one label for every visible part and excludes filtered components', async () => {
  const print = vi.spyOn(window, 'print').mockImplementation(() => undefined)
  render(<MemoryRouter><ParkScopeContext.Provider value={{ parkId: 7, selectedPark: park, parks: [park], loading: false, locked: true, setParkId: vi.fn(), refreshParks: vi.fn() }}><InventoryPage apiClient={inventoryClient({ inventory: vi.fn(async () => stockWithTwoComponents) })} /></ParkScopeContext.Provider></MemoryRouter>)

  await screen.findByRole('heading', { name: 'Подвязка' })
  await userEvent.click(screen.getByRole('button', { name: 'Печать этикеток' }))

  expect(document.querySelectorAll('.inventory-print-label')).toHaveLength(2)
  expect(document.querySelector('.inventory-print-sheet')).toHaveTextContent('TY-001')
  await waitFor(() => expect(print).toHaveBeenCalledTimes(1))

  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Компонента' }), '9')
  await userEvent.click(screen.getByRole('button', { name: 'Печать этикеток' }))

  expect(document.querySelectorAll('.inventory-print-label')).toHaveLength(1)
  expect(document.querySelector('.inventory-print-sheet')).toHaveTextContent('WH-001')
  await waitFor(() => expect(print).toHaveBeenCalledTimes(2))
})

it('shows stock location and prepares a printable shelf label', async () => {
  const print = vi.spyOn(window, 'print').mockImplementation(() => undefined)
  render(<MemoryRouter><ParkScopeContext.Provider value={{ parkId: 7, selectedPark: park, parks: [park], loading: false, locked: true, setParkId: vi.fn(), refreshParks: vi.fn() }}><InventoryPage apiClient={inventoryClient()} /></ParkScopeContext.Provider></MemoryRouter>)
  expect(await screen.findByText('Стеллаж A / полка 2')).toBeVisible()
  await userEvent.click(screen.getByRole('button', { name: 'Распечатать этикетку' }))
  await waitFor(() => expect(print).toHaveBeenCalled())
  const label = document.querySelector('.inventory-print-label')!
  expect(document.querySelectorAll('.inventory-print-label')).toHaveLength(1)
  expect(label).toHaveTextContent('Тяга')
  expect(label).toHaveTextContent('TY-001')
  expect(label).toHaveTextContent('Стеллаж A / полка 2')
})

it('writes a selected part off from the current task', async () => {
  const writeoff = vi.fn(async () => ({ id: 1, part_id: 3, park_id: 7, actor_user_id: 4, actor_username: 'mech', kind: 'task_writeoff', delta: -2, balance_after: 3, issue_key: 'RP-42', note: null, created_at: '2026-09-10T10:00:00Z' }))
  render(<TaskPartsPanel apiClient={inventoryClient({ writeoffInventoryForTask: writeoff })} issueKey="RP-42" parkId={7} />)
  await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Компонента' }), '2')
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Запчасть' }), '3')
  await userEvent.clear(screen.getByRole('spinbutton', { name: 'Списать, шт.' }))
  await userEvent.type(screen.getByRole('spinbutton', { name: 'Списать, шт.' }), '2')
  await userEvent.click(screen.getByRole('button', { name: 'Списать в задачу' }))
  await waitFor(() => expect(writeoff).toHaveBeenCalledWith('RP-42', 3, 2))
})

it('resets an invalid write-off quantity after inventory refresh', async () => {
  const refreshedStock: InventoryOverview = { ...stock, components: [{ ...stock.components[0], parts: [{ ...stock.components[0].parts[0], quantity: 1 }] }] }
  const client = inventoryClient({ inventory: vi.fn().mockResolvedValueOnce(stock).mockResolvedValueOnce(refreshedStock), writeoffInventoryForTask: vi.fn(async () => ({ id: 1, part_id: 3, park_id: 7, actor_user_id: 4, actor_username: 'mech', kind: 'task_writeoff', delta: -2, balance_after: 1, issue_key: 'RP-42', note: null, created_at: '2026-09-10T10:00:00Z' })) })
  render(<TaskPartsPanel apiClient={client} issueKey="RP-42" parkId={7} />)

  await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Компонента' }), '2')
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Запчасть' }), '3')
  await userEvent.clear(screen.getByRole('spinbutton', { name: 'Списать, шт.' }))
  await userEvent.type(screen.getByRole('spinbutton', { name: 'Списать, шт.' }), '2')
  await userEvent.click(screen.getByRole('button', { name: 'Списать в задачу' }))

  await waitFor(() => expect(client.inventory).toHaveBeenCalledTimes(2))
  expect(screen.getByRole('spinbutton', { name: 'Списать, шт.' })).toHaveValue(1)
})
