import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, type InventoryCatalogSearchItem, type InventoryCount, type InventoryCountLineInput } from '../../api'
import { InventoryCountsView } from './InventoryCountsView'

const count: InventoryCount = { id: 71, park_id: 7, name: 'Сентябрь', status: 'draft', created_by: 1, posted_by: null, created_at: '2026-09-12T10:00:00Z', posted_at: null, lines: [{ id: 1, catalog_part_id: 31, expected_quantity: '5', actual_quantity: null, difference: null, comment: null }] }
const part: InventoryCatalogSearchItem = { id: 31, component_id: 4, component_name: 'Подвязка', name: 'Тяга', article: 'ABC-1', is_active: true, has_photo: false, quantity: '5', minimum_quantity: '2', location: null, stock_is_active: true }
function client(overrides = {}) {
  return {
    inventoryCounts: vi.fn(async () => ({ items: [], limit: 25, offset: 0, total: 0 })),
    searchInventory: vi.fn(async () => ({ items: [part], limit: 25, offset: 0, total: 1 })),
    createInventoryCount: vi.fn(async () => count),
    updateInventoryCount: vi.fn(async (_park: number, _id: number, lines: InventoryCountLineInput[]): Promise<InventoryCount> => ({ ...count, lines: [{ ...count.lines[0], actual_quantity: lines[0].actual_quantity, difference: '3' }] })),
    postInventoryCount: vi.fn(async () => ({ ...count, status: 'posted' as const })),
    cancelInventoryCount: vi.fn(async () => ({ ...count, status: 'cancelled' as const })),
    ...overrides,
  }
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('shows differences and retains actual quantities beside stale conflicts for retry', async () => {
  const stale = new ApiError(409, { code: 'inventory_count_stale', conflicts: [{ catalog_part_id: 31, expected_quantity: '5', current_quantity: '6' }] })
  const apiClient = client({ postInventoryCount: vi.fn().mockRejectedValueOnce(stale).mockResolvedValueOnce({ ...count, status: 'posted' }) })
  const user = userEvent.setup()
  render(<InventoryCountsView apiClient={apiClient} parkId={7} />)
  await user.click(await screen.findByRole('button', { name: 'Новая инвентаризация' }))
  await user.type(screen.getByLabelText('Название акта'), 'Сентябрь')
  await user.click(screen.getByRole('button', { name: 'Создать акт' }))
  const actual = await screen.findByRole('textbox', { name: 'Фактически ABC-1' })
  await user.type(actual, '8')
  expect(screen.getByText('Разница: +3')).toBeVisible()
  await user.click(screen.getByRole('button', { name: 'Провести акт' }))
  await user.click(await screen.findByRole('button', { name: 'Подтвердить проведение' }))
  expect(await screen.findByText('Ожидалось 5, сейчас 6')).toBeVisible()
  expect(actual).toHaveValue('8')
  await user.click(screen.getByRole('button', { name: 'Повторить проведение' }))
  await user.click(await screen.findByRole('button', { name: 'Подтвердить проведение' }))
  await waitFor(() => expect(apiClient.postInventoryCount).toHaveBeenCalledTimes(2))
})

it('pages and searches counts on the server', async () => {
  const inventoryCounts = vi.fn(async ({ offset }: { query?: string; offset?: number }) => ({ items: [count], limit: 25, offset: offset ?? 0, total: 60 }))
  render(<InventoryCountsView apiClient={client({ inventoryCounts })} parkId={7} />)
  await userEvent.type(await screen.findByRole('searchbox', { name: 'Найти инвентаризацию' }), 'Сентябрь')
  await waitFor(() => expect(inventoryCounts).toHaveBeenCalledWith(7, { query: 'Сентябрь', limit: 25, offset: 0 }))
  await userEvent.click(screen.getByRole('button', { name: 'Следующая страница актов' }))
  await waitFor(() => expect(inventoryCounts).toHaveBeenLastCalledWith(7, { query: 'Сентябрь', limit: 25, offset: 25 }))
  expect(within(await screen.findByRole('article', { name: 'Инвентаризация №71' })).getByText('Сентябрь')).toBeVisible()
})
