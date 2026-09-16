import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, type InventoryCatalogSearchItem, type InventoryReceipt } from '../../api'
import { InventoryReceiptsView } from './InventoryReceiptsView'

const part: InventoryCatalogSearchItem = { id: 31, component_id: 4, component_name: 'Подвязка', name: 'Тяга', article: 'ABC-1', is_active: true, has_photo: false, quantity: '5', minimum_quantity: '2', location: 'А-1', stock_is_active: true }
const receipt: InventoryReceipt = { id: 91, park_id: 7, revision: 'original', supplier: 'Завод', document_number: null, received_on: '2026-09-12', comment: null, status: 'draft', created_by: 1, posted_by: null, created_at: '2026-09-12T10:00:00Z', posted_at: null, lines: [{ id: 1, catalog_part_id: 31, catalog_part_name: 'Тяга', catalog_part_article: 'ABC-1', catalog_component_id: 4, catalog_component_name: 'Подвязка', quantity: '10', note: null }] }

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
  expect(postInventoryReceipt).toHaveBeenNthCalledWith(2, 7, 91, 'original')
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
  await waitFor(() => expect(apiClient.postInventoryReceipt).toHaveBeenCalledWith(7, 91, 'original'))
  expect(apiClient.updateInventoryReceipt).not.toHaveBeenCalled()
})

it('posts an unchanged historical receipt directly', async () => {
  const historical = { ...receipt, lines: [{ ...receipt.lines[0], catalog_part_name: 'Источник A', catalog_part_article: 'A-OLD' }] }
  const apiClient = client({ inventoryReceipts: vi.fn(async () => ({ items: [historical], limit: 25, offset: 0, total: 1 })) })
  render(<InventoryReceiptsView apiClient={apiClient} parkId={7} permissions={['inventory.stock.manage', 'inventory.documents.post']} />)
  await userEvent.click(within(await screen.findByRole('article', { name: 'Поставка №91' })).getByRole('button', { name: 'Открыть' }))
  expect(screen.getByText('Источник A · A-OLD')).toBeVisible()
  await userEvent.click(screen.getByRole('button', { name: 'Провести поставку' }))
  await userEvent.click(await screen.findByRole('button', { name: 'Подтвердить проведение' }))
  await waitFor(() => expect(apiClient.postInventoryReceipt).toHaveBeenCalledWith(7, 91, 'original'))
  expect(apiClient.updateInventoryReceipt).not.toHaveBeenCalled()
})

it.each([
  ['Поставщик или завод', 'Другой завод', '  Завод  '],
  ['Номер документа', 'D-1', '  '],
  ['Комментарий', 'Изменено', '  '],
  ['Дата поставки', '2026-09-13', '2026-09-12'],
  ['Количество A-OLD', '11', '10'],
  ['Количество A-OLD', '11', '010'],
])('preserves merged source identity after editing and reverting %s to %s then %s', async (label, changed, reverted) => {
  let stored: InventoryReceipt = { ...receipt, lines: [{ ...receipt.lines[0], catalog_part_name: 'Источник A', catalog_part_article: 'A-OLD', note: 'Исходная заметка' }] }
  const apiClient = client({
    inventoryReceipts: vi.fn(async () => ({ items: [stored], limit: 25, offset: 0, total: 1 })),
    updateInventoryReceipt: vi.fn(async () => {
      stored = { ...stored, lines: [{ ...stored.lines[0], catalog_part_id: 32, catalog_part_name: 'Цель B', catalog_part_article: 'B-NEW' }] }
      return stored
    }),
    postInventoryReceipt: vi.fn(async () => { stored = { ...stored, status: 'posted' }; return stored }),
  })
  render(<InventoryReceiptsView apiClient={apiClient} parkId={7} />)
  await userEvent.click(within(await screen.findByRole('article', { name: 'Поставка №91' })).getByRole('button', { name: 'Открыть' }))
  fireEvent.change(screen.getByLabelText(label), { target: { value: changed } })
  fireEvent.change(screen.getByLabelText(label), { target: { value: reverted } })
  await userEvent.click(screen.getByRole('button', { name: 'Провести поставку' }))
  await userEvent.click(await screen.findByRole('button', { name: 'Подтвердить проведение' }))
  expect(await screen.findByText('Поставка проведена')).toBeVisible()
  expect(apiClient.updateInventoryReceipt).not.toHaveBeenCalled()
  expect(stored.lines[0]).toMatchObject({ id: 1, catalog_part_id: 31, note: 'Исходная заметка' })
  expect(screen.getByText('Источник A · A-OLD')).toBeVisible()
})

it('does not patch after adding and removing an extra receipt line', async () => {
  const extra = { ...part, id: 32, article: 'NEW-2' }
  const apiClient = client({ inventoryReceipts: vi.fn(async () => ({ items: [receipt], limit: 25, offset: 0, total: 1 })), searchInventory: vi.fn(async () => ({ items: [extra], limit: 25, offset: 0, total: 1 })) })
  render(<InventoryReceiptsView apiClient={apiClient} parkId={7} />)
  await userEvent.click(within(await screen.findByRole('article', { name: 'Поставка №91' })).getByRole('button', { name: 'Открыть' }))
  await userEvent.type(screen.getByRole('searchbox', { name: 'Найти запчасть для поставки' }), 'NEW')
  await userEvent.click(await screen.findByRole('button', { name: 'Добавить NEW-2' }))
  await userEvent.click(screen.getByRole('button', { name: 'Удалить NEW-2' }))
  expect(screen.getByRole('button', { name: 'Сохранить черновик' })).toBeDisabled()
  await userEvent.click(screen.getByRole('button', { name: 'Провести поставку' }))
  await userEvent.click(await screen.findByRole('button', { name: 'Подтвердить проведение' }))
  expect(await screen.findByText('Поставка проведена')).toBeVisible()
  expect(apiClient.updateInventoryReceipt).not.toHaveBeenCalled()
})

it.each([
  { change: 'order only', quantity: '1', note: null, patches: 0 },
  { change: 'quantity', quantity: '2', note: null, patches: 1 },
  { change: 'note removed', quantity: '1', note: 'Original note', patches: 1 },
])('compares re-added active lines by content: $change', async ({ quantity, note, patches }) => {
  const active = { ...part, id: 32, article: 'B-NEW', name: 'Цель B' }
  let stored: InventoryReceipt = { ...receipt, lines: [
    { ...receipt.lines[0], id: 2, catalog_part_id: 32, catalog_part_article: 'B-NEW', catalog_part_name: 'Цель B', quantity: '1', note },
    { ...receipt.lines[0], catalog_part_article: 'A-OLD', catalog_part_name: 'Источник A', quantity: '9007199254740993', note: 'Historical note' },
  ] }
  const apiClient = client({
    inventoryReceipts: vi.fn(async () => ({ items: [stored], limit: 25, offset: 0, total: 1 })),
    searchInventory: vi.fn(async () => ({ items: [active], limit: 25, offset: 0, total: 1 })),
    updateInventoryReceipt: vi.fn(async () => stored),
    postInventoryReceipt: vi.fn(async () => { stored = { ...stored, status: 'posted' }; return stored }),
  })
  render(<InventoryReceiptsView apiClient={apiClient} parkId={7} />)
  await userEvent.click(within(await screen.findByRole('article', { name: 'Поставка №91' })).getByRole('button', { name: 'Открыть' }))
  await userEvent.click(screen.getByRole('button', { name: 'Удалить B-NEW' }))
  await userEvent.type(screen.getByRole('searchbox', { name: 'Найти запчасть для поставки' }), 'B-NEW')
  await userEvent.click(await screen.findByRole('button', { name: 'Добавить B-NEW' }))
  fireEvent.change(screen.getByLabelText('Количество B-NEW'), { target: { value: quantity } })
  expect(screen.getAllByRole('textbox', { name: /^Количество/ }).map(input => input.getAttribute('id'))).toEqual(['receipt-quantity-31', 'receipt-quantity-32'])
  if (!patches) expect(screen.getByRole('button', { name: 'Сохранить черновик' })).toBeDisabled()
  else expect(screen.getByRole('button', { name: 'Сохранить черновик' })).toBeEnabled()
  await userEvent.click(screen.getByRole('button', { name: 'Провести поставку' }))
  await userEvent.click(await screen.findByRole('button', { name: 'Подтвердить проведение' }))
  expect(await screen.findByText('Поставка проведена')).toBeVisible()
  expect(apiClient.updateInventoryReceipt).toHaveBeenCalledTimes(patches)
  if (patches) expect(apiClient.updateInventoryReceipt).toHaveBeenCalledWith(7, 91, expect.objectContaining({ lines: [
    { catalog_part_id: 31, quantity: '9007199254740993', note: 'Historical note' },
    { catalog_part_id: 32, quantity, note: null },
  ] }))
  expect(apiClient.postInventoryReceipt).toHaveBeenCalledWith(7, 91, 'original')
  expect(screen.getByText('Источник A · A-OLD')).toBeVisible()
  expect(stored.lines[1]).toMatchObject({ id: 1, catalog_part_id: 31, note: 'Historical note' })
})

it('posts a supplier-null draft for a post-only user without a silent dialog no-op', async () => {
  const withoutSupplier = { ...receipt, supplier: null }
  const apiClient = client({ inventoryReceipts: vi.fn(async () => ({ items: [withoutSupplier], limit: 25, offset: 0, total: 1 })) })
  render(<InventoryReceiptsView apiClient={apiClient} parkId={7} permissions={['inventory.documents.post']} />)
  await userEvent.click(within(await screen.findByRole('article', { name: 'Поставка №91' })).getByRole('button', { name: 'Открыть' }))
  await userEvent.click(screen.getByRole('button', { name: 'Провести поставку' }))
  await userEvent.click(await screen.findByRole('button', { name: 'Подтвердить проведение' }))
  await waitFor(() => expect(apiClient.postInventoryReceipt).toHaveBeenCalledWith(7, 91, 'original'))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

it('patches a real edit once and uses the saved baseline on a failed post retry', async () => {
  const saved = { ...receipt, revision: 'saved', comment: 'Изменено', lines: [{ ...receipt.lines[0], note: 'Заметка' }] }
  const apiClient = client({
    inventoryReceipts: vi.fn(async () => ({ items: [receipt], limit: 25, offset: 0, total: 1 })),
    updateInventoryReceipt: vi.fn(async () => saved),
    postInventoryReceipt: vi.fn().mockRejectedValueOnce(new ApiError(500, null)).mockResolvedValueOnce({ ...saved, status: 'posted' }),
  })
  render(<InventoryReceiptsView apiClient={apiClient} parkId={7} permissions={['inventory.stock.manage', 'inventory.documents.post']} />)
  await userEvent.click(within(await screen.findByRole('article', { name: 'Поставка №91' })).getByRole('button', { name: 'Открыть' }))
  await userEvent.type(screen.getByLabelText('Комментарий'), '  Изменено  ')
  await userEvent.click(screen.getByRole('button', { name: 'Провести поставку' }))
  await userEvent.click(await screen.findByRole('button', { name: 'Подтвердить проведение' }))
  await waitFor(() => expect(apiClient.updateInventoryReceipt).toHaveBeenCalledTimes(1))
  expect(apiClient.updateInventoryReceipt).toHaveBeenCalledWith(7, 91, { revision: 'original', supplier: 'Завод', document_number: null, received_on: '2026-09-12', comment: 'Изменено', lines: [{ catalog_part_id: 31, quantity: '10', note: null }] })
  expect(await screen.findByText('Не удалось изменить поставку.')).toBeVisible()
  expect(screen.getByLabelText('Комментарий')).toHaveValue('Изменено')
  fireEvent.change(screen.getByLabelText('Комментарий'), { target: { value: 'Другое' } })
  fireEvent.change(screen.getByLabelText('Комментарий'), { target: { value: ' Изменено ' } })
  expect(screen.getByRole('button', { name: 'Сохранить черновик' })).toBeDisabled()
  await userEvent.click(screen.getByRole('button', { name: 'Провести поставку' }))
  await userEvent.click(await screen.findByRole('button', { name: 'Подтвердить проведение' }))
  expect(await screen.findByText('Поставка проведена')).toBeVisible()
  expect(apiClient.updateInventoryReceipt).toHaveBeenCalledTimes(1)
  expect(apiClient.postInventoryReceipt).toHaveBeenCalledTimes(2)
  expect(apiClient.postInventoryReceipt).toHaveBeenCalledWith(7, 91, 'saved')
})

it('preserves local edits and requires refresh after a concurrent draft change', async () => {
  const newer = { ...receipt, revision: 'newer', lines: [{ ...receipt.lines[0], quantity: '12' as const }] }
  let stored = receipt
  const apiClient = client({
    inventoryReceipts: vi.fn(async () => ({ items: [stored], limit: 25, offset: 0, total: 1 })),
    updateInventoryReceipt: vi.fn(async () => { throw new ApiError(409, { code: 'inventory_receipt_stale' }) }),
  })
  render(<InventoryReceiptsView apiClient={apiClient} parkId={7} />)
  await userEvent.click(within(await screen.findByRole('article', { name: 'Поставка №91' })).getByRole('button', { name: 'Открыть' }))
  fireEvent.change(screen.getByLabelText('Количество ABC-1'), { target: { value: '11' } })
  stored = newer
  await userEvent.click(screen.getByRole('button', { name: 'Сохранить черновик' }))

  expect(apiClient.updateInventoryReceipt).toHaveBeenCalledWith(7, 91, expect.objectContaining({ revision: 'original' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Поставка изменена другим пользователем')
  expect(screen.getByLabelText('Количество ABC-1')).toHaveValue('11')
  expect(screen.getByRole('button', { name: 'Сохранить черновик' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Провести поставку' })).toBeDisabled()
  await userEvent.click(screen.getByRole('button', { name: 'Обновить поставку' }))
  await userEvent.click(within(await screen.findByRole('article', { name: 'Поставка №91' })).getByRole('button', { name: 'Открыть' }))
  expect(screen.getByLabelText('Количество ABC-1')).toHaveValue('12')
  expect(apiClient.updateInventoryReceipt).toHaveBeenCalledTimes(1)
})

it('keeps locally edited lines when a refreshed list reports a newer revision', async () => {
  let stored = receipt
  const apiClient = client({ inventoryReceipts: vi.fn(async () => ({ items: [stored], limit: 25, offset: 0, total: 1 })) })
  const { rerender } = render(<InventoryReceiptsView apiClient={apiClient} parkId={7} refreshVersion={0} />)
  await userEvent.click(within(await screen.findByRole('article', { name: 'Поставка №91' })).getByRole('button', { name: 'Открыть' }))
  fireEvent.change(screen.getByLabelText('Количество ABC-1'), { target: { value: '11' } })
  stored = { ...receipt, revision: 'newer', lines: [{ ...receipt.lines[0], quantity: '12' }] }

  rerender(<InventoryReceiptsView apiClient={apiClient} parkId={7} refreshVersion={1} />)

  expect(await screen.findByRole('alert')).toHaveTextContent('Поставка изменена другим пользователем')
  expect(screen.getByLabelText('Количество ABC-1')).toHaveValue('11')
  expect(screen.getByRole('button', { name: 'Сохранить черновик' })).toBeDisabled()
  expect(apiClient.updateInventoryReceipt).not.toHaveBeenCalled()
})

it.each([
  ['Провести поставку', 'Подтвердить проведение', 'postInventoryReceipt'],
  ['Отменить черновик', 'Подтвердить отмену', 'cancelInventoryReceipt'],
] as const)('guards an unchanged draft when %s encounters a newer revision', async (action, confirmation, method) => {
  const apiClient = client({
    inventoryReceipts: vi.fn(async () => ({ items: [receipt], limit: 25, offset: 0, total: 1 })),
    [method]: vi.fn(async () => { throw new ApiError(409, { code: 'inventory_receipt_stale' }) }),
  })
  render(<InventoryReceiptsView apiClient={apiClient} parkId={7} />)
  await userEvent.click(within(await screen.findByRole('article', { name: 'Поставка №91' })).getByRole('button', { name: 'Открыть' }))
  await userEvent.click(screen.getByRole('button', { name: action }))
  await userEvent.click(await screen.findByRole('button', { name: confirmation }))

  expect(apiClient[method]).toHaveBeenCalledWith(7, 91, 'original')
  expect(await screen.findByRole('alert')).toHaveTextContent('Поставка изменена другим пользователем')
  expect(screen.getByRole('button', { name: 'Сохранить черновик' })).toBeDisabled()
})

it.each([
  ['Провести поставку', 'Подтвердить проведение', 'postInventoryReceipt'],
  ['Отменить черновик', 'Подтвердить отмену', 'cancelInventoryReceipt'],
] as const)('closes %s confirmation when another user changes the receipt', async (action, confirmation, method) => {
  let stored = receipt
  const apiClient = client({ inventoryReceipts: vi.fn(async () => ({ items: [stored], limit: 25, offset: 0, total: 1 })) })
  const { rerender } = render(<InventoryReceiptsView apiClient={apiClient} parkId={7} refreshVersion={0} />)
  await userEvent.click(within(await screen.findByRole('article', { name: 'Поставка №91' })).getByRole('button', { name: 'Открыть' }))
  await userEvent.click(screen.getByRole('button', { name: action }))
  expect(screen.getByRole('button', { name: confirmation })).toBeInTheDocument()

  stored = { ...receipt, revision: 'newer', lines: [{ ...receipt.lines[0], quantity: '100' }] }
  rerender(<InventoryReceiptsView apiClient={apiClient} parkId={7} refreshVersion={1} />)

  expect(await screen.findByRole('alert')).toHaveTextContent('Поставка изменена другим пользователем')
  expect(screen.queryByRole('button', { name: confirmation })).not.toBeInTheDocument()
  expect(apiClient[method]).not.toHaveBeenCalled()
})
