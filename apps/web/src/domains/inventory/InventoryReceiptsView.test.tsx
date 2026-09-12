import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import type { InventoryCatalogSearchItem, InventoryReceipt } from '../../api'
import { InventoryReceiptsView } from './InventoryReceiptsView'

const part: InventoryCatalogSearchItem = { id: 31, component_id: 4, component_name: 'Подвязка', name: 'Тяга', article: 'ABC-1', is_active: true, has_photo: false, quantity: '5', minimum_quantity: '2', location: 'А-1', stock_is_active: true }
const receipt: InventoryReceipt = { id: 91, park_id: 7, supplier: 'Завод', document_number: null, received_on: '2026-09-12', comment: null, status: 'draft', created_by: 1, posted_by: null, created_at: '2026-09-12T10:00:00Z', posted_at: null, lines: [{ id: 1, catalog_part_id: 31, quantity: '10', note: null }] }

function client(overrides = {}) {
  return {
    inventoryReceipts: vi.fn(async () => ({ items: [], limit: 25, offset: 0, total: 0 })),
    searchInventory: vi.fn(async () => ({ items: [part], limit: 25, offset: 0, total: 1 })),
    createInventoryReceipt: vi.fn(async () => receipt),
    updateInventoryReceipt: vi.fn(async () => receipt),
    postInventoryReceipt: vi.fn(async () => ({ ...receipt, status: 'posted' as const, posted_at: '2026-09-12T11:00:00Z' })),
    cancelInventoryReceipt: vi.fn(async () => ({ ...receipt, status: 'cancelled' as const })),
    reverseInventoryReceipt: vi.fn(async () => ({ ...receipt, id: 92, status: 'posted' as const })),
    ...overrides,
  }
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('creates and posts one receipt, combines duplicate lines and preserves int64 strings', async () => {
  const apiClient = client()
  const user = userEvent.setup()
  render(<InventoryReceiptsView apiClient={apiClient} parkId={7} />)
  await user.click(await screen.findByRole('button', { name: 'Новая поставка' }))
  await user.type(screen.getByLabelText('Поставщик или завод'), 'Завод')
  await user.type(screen.getByRole('searchbox', { name: 'Найти запчасть для поставки' }), 'ABC-1')
  await user.click(await screen.findByRole('button', { name: 'Добавить ABC-1' }))
  const quantity = screen.getByRole('textbox', { name: 'Количество ABC-1' })
  await user.clear(quantity); await user.type(quantity, '9007199254740993')
  await user.click(screen.getByRole('button', { name: 'Добавить ABC-1' }))
  expect(screen.getAllByRole('textbox', { name: 'Количество ABC-1' })).toHaveLength(1)
  await user.click(screen.getByRole('button', { name: 'Провести поставку' }))
  await user.click(await screen.findByRole('button', { name: 'Подтвердить проведение' }))

  await waitFor(() => expect(apiClient.createInventoryReceipt).toHaveBeenCalledWith(7, expect.objectContaining({ supplier: 'Завод', lines: [{ catalog_part_id: 31, quantity: '9007199254740994', note: null }] })))
  expect(apiClient.postInventoryReceipt).toHaveBeenCalledTimes(1)
  expect(await screen.findByText('Поставка проведена')).toBeVisible()
})

it('uses a single mobile editor and returns to the document list', async () => {
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: true, media: '(max-width: 599px)', addEventListener: vi.fn(), removeEventListener: vi.fn() }))
  render(<InventoryReceiptsView apiClient={client({ inventoryReceipts: vi.fn(async () => ({ items: [receipt], limit: 25, offset: 0, total: 1 })) })} parkId={7} />)
  const card = await screen.findByRole('article', { name: 'Поставка №91' })
  await userEvent.click(within(card).getByRole('button', { name: 'Открыть' }))
  expect(screen.queryByRole('article', { name: 'Поставка №91' })).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Назад к поставкам' }))
  expect(await screen.findByRole('article', { name: 'Поставка №91' })).toBeVisible()
})
