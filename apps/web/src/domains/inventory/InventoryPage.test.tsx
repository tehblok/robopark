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

function inventoryClient(overrides = {}) {
  return { ...api, inventory: vi.fn(async () => stock), ...overrides }
}

it('shows stock location and prepares a printable shelf label', async () => {
  const print = vi.spyOn(window, 'print').mockImplementation(() => undefined)
  render(<MemoryRouter><ParkScopeContext.Provider value={{ parkId: 7, selectedPark: park, parks: [park], loading: false, locked: true, setParkId: vi.fn(), refreshParks: vi.fn() }}><InventoryPage apiClient={inventoryClient()} /></ParkScopeContext.Provider></MemoryRouter>)
  expect(await screen.findByText('Стеллаж A / полка 2')).toBeVisible()
  await userEvent.click(screen.getByRole('button', { name: 'Распечатать этикетку' }))
  await waitFor(() => expect(print).toHaveBeenCalled())
  const label = document.querySelector('.inventory-print-label')!
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
