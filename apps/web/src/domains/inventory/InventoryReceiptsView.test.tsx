import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, type InventoryCatalogSearchItem, type InventoryReceipt } from '../../api'
import { InventoryReceiptsView } from './InventoryReceiptsView'

const part: InventoryCatalogSearchItem = { id: 31, component_id: 4, component_name: 'Подвязка', name: 'Тяга', article: 'ABC-1', is_active: true, has_photo: false, quantity: '5', minimum_quantity: '2', location: 'А-1', stock_is_active: true }
const receipt: InventoryReceipt = { id: 91, park_id: 7, supplier: 'Завод', document_number: null, received_on: '2026-09-12', comment: null, status: 'draft', created_by: 1, posted_by: null, created_at: '2026-09-12T10:00:00Z', posted_at: null, lines: [{ id: 1, catalog_part_id: 31, catalog_part_name: 'Тяга', catalog_part_article: 'ABC-1', catalog_component_id: 4, catalog_component_name: 'Подвязка', quantity: '10', note: null }] }

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
  render(<InventoryReceiptsView apiClient={apiClient} parkId={7} permissions={['inventory.stock.manage', 'inventory.documents.post']} />)
  await user.click(await screen.findByRole('button', { name: 'Новая поставка' }))
  await user.type(screen.getByLabelText('Поставщик или завод'), 'Завод')
  await user.type(screen.getByRole('searchbox', { name: 'Найти запчасть для поставки' }), 'ABC-1')
  await user.click(await screen.findByRole('button', { name: 'Добавить ABC-1' }))
  const quantity = screen.getByRole('textbox', { name: 'Количество ABC-1' })
  await user.clear(quantity); await user.type(quantity, '9007199254740993')
  await user.click(screen.getByRole('button', { name: 'Добавить ABC-1' }))
  expect(screen.getAllByRole('textbox', { name: 'Количество ABC-1' })).toHaveLength(1)
  expect(screen.getByRole('button', { name: 'Сохранить черновик' })).toBeVisible()
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

it('preserves server draft lines when another catalog part is added', async () => {
  const other = { ...part, id: 32, article: 'NEW-2', name: 'Новая' }
  render(<InventoryReceiptsView apiClient={client({ inventoryReceipts: vi.fn(async () => ({ items: [receipt], limit: 25, offset: 0, total: 1 })), searchInventory: vi.fn(async () => ({ items: [other], limit: 25, offset: 0, total: 1 })) })} parkId={7} />)
  await userEvent.click(within(await screen.findByRole('article', { name: 'Поставка №91' })).getByRole('button', { name: 'Открыть' }))
  await userEvent.type(screen.getByRole('searchbox', { name: 'Найти запчасть для поставки' }), 'NEW')
  await userEvent.click(await screen.findByRole('button', { name: 'Добавить NEW-2' }))
  expect(screen.getByRole('textbox', { name: 'Количество ABC-1' })).toHaveValue('10')
  expect(screen.getByRole('textbox', { name: 'Количество NEW-2' })).toHaveValue('1')
})

it('keeps the created receipt id for a failed post retry and blocks duplicate confirmation', async () => {
  let rejectPost!: (reason: unknown) => void
  const firstPost = new Promise<InventoryReceipt>((_resolve, reject) => { rejectPost = reject })
  const postInventoryReceipt = vi.fn().mockImplementationOnce(() => firstPost).mockResolvedValueOnce({ ...receipt, status: 'posted' })
  const apiClient = client({ postInventoryReceipt })
  render(<InventoryReceiptsView apiClient={apiClient} parkId={7} />)
  await userEvent.click(await screen.findByRole('button', { name: 'Новая поставка' }))
  await userEvent.type(screen.getByLabelText('Поставщик или завод'), 'Завод')
  await userEvent.type(screen.getByRole('searchbox', { name: 'Найти запчасть для поставки' }), 'ABC')
  await userEvent.click(await screen.findByRole('button', { name: 'Добавить ABC-1' }))
  await userEvent.click(screen.getByRole('button', { name: 'Провести поставку' })); const confirm = await screen.findByRole('button', { name: 'Подтвердить проведение' }); await userEvent.dblClick(confirm)
  await waitFor(() => expect(postInventoryReceipt).toHaveBeenCalledTimes(1))
  await act(async () => rejectPost(new ApiError(500, null)))
  expect(await screen.findByText('Не удалось изменить поставку.')).toBeVisible()
  expect(apiClient.createInventoryReceipt).toHaveBeenCalledTimes(1)
  expect(screen.getByRole('heading', { name: 'Поставка №91' })).toBeVisible()
  await userEvent.click(screen.getByRole('button', { name: 'Провести поставку' })); await userEvent.click(await screen.findByRole('button', { name: 'Подтвердить проведение' }))
  expect(apiClient.createInventoryReceipt).toHaveBeenCalledTimes(1)
  expect(apiClient.updateInventoryReceipt).not.toHaveBeenCalled()
  expect(postInventoryReceipt).toHaveBeenNthCalledWith(2, 7, 91)
})

it('shows list failures with retry and enforces effective permissions', async () => {
  const inventoryReceipts = vi.fn().mockRejectedValueOnce(new ApiError(500, null)).mockResolvedValueOnce({ items: [receipt], limit: 25, offset: 0, total: 1 })
  render(<InventoryReceiptsView apiClient={client({ inventoryReceipts })} parkId={7} permissions={[]} />)
  expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось загрузить поставки.')
  await userEvent.click(screen.getByRole('button', { name: 'Повторить загрузку поставок' }))
  const card = await screen.findByRole('article', { name: 'Поставка №91' })
  expect(screen.queryByRole('button', { name: 'Новая поставка' })).not.toBeInTheDocument()
  await userEvent.click(within(card).getByRole('button', { name: 'Открыть' }))
  expect(screen.queryByRole('button', { name: 'Провести поставку' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Отменить черновик' })).not.toBeInTheDocument()
  expect(screen.queryByRole('textbox', { name: 'Поставщик или завод' })).not.toBeInTheDocument()
  expect(screen.queryByRole('searchbox', { name: 'Найти запчасть для поставки' })).not.toBeInTheDocument()
})

it('lets manage-only users save and update drafts without posting', async () => {
  const apiClient = client()
  render(<InventoryReceiptsView apiClient={apiClient} parkId={7} permissions={['inventory.stock.manage']} />)
  await userEvent.click(await screen.findByRole('button', { name: 'Новая поставка' }))
  await userEvent.type(screen.getByLabelText('Поставщик или завод'), 'Завод')
  await userEvent.type(screen.getByRole('searchbox', { name: 'Найти запчасть для поставки' }), 'ABC')
  await userEvent.click(await screen.findByRole('button', { name: 'Добавить ABC-1' }))
  await userEvent.dblClick(screen.getByRole('button', { name: 'Сохранить черновик' }))
  await waitFor(() => expect(apiClient.createInventoryReceipt).toHaveBeenCalledTimes(1))
  expect(apiClient.postInventoryReceipt).not.toHaveBeenCalled()
  expect(screen.queryByRole('button', { name: 'Провести поставку' })).not.toBeInTheDocument()
  await userEvent.type(screen.getByLabelText('Номер документа'), 'D-1')
  await userEvent.click(screen.getByRole('button', { name: 'Сохранить черновик' }))
  expect(apiClient.updateInventoryReceipt).toHaveBeenCalledWith(7, 91, expect.objectContaining({ document_number: 'D-1' }))
  expect(screen.getByRole('button', { name: 'Отменить черновик' })).toBeVisible()
})

it('lets post-only users post an existing receipt without editing it', async () => {
  const apiClient = client({ inventoryReceipts: vi.fn(async () => ({ items: [receipt], limit: 25, offset: 0, total: 1 })) })
  render(<InventoryReceiptsView apiClient={apiClient} parkId={7} permissions={['inventory.documents.post']} />)
  await userEvent.click(within(await screen.findByRole('article', { name: 'Поставка №91' })).getByRole('button', { name: 'Открыть' }))
  expect(screen.getByText('Тяга · ABC-1')).toBeVisible()
  expect(screen.queryByRole('button', { name: 'Сохранить черновик' })).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Провести поставку' }))
  await userEvent.click(await screen.findByRole('button', { name: 'Подтвердить проведение' }))
  await waitFor(() => expect(apiClient.postInventoryReceipt).toHaveBeenCalledWith(7, 91))
  expect(apiClient.updateInventoryReceipt).not.toHaveBeenCalled()
})
