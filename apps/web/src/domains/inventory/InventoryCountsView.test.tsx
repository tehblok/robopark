import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, type InventoryCatalogSearchItem, type InventoryCount, type InventoryCountLineInput } from '../../api'
import { InventoryCountsView } from './InventoryCountsView'

const count: InventoryCount = { id: 71, park_id: 7, name: 'Сентябрь', status: 'draft', created_by: 1, posted_by: null, created_at: '2026-09-12T10:00:00Z', posted_at: null, lines: [{ id: 1, catalog_part_id: 31, catalog_part_name: 'Тяга', catalog_part_article: 'ABC-1', catalog_component_id: 4, catalog_component_name: 'Подвязка', expected_quantity: '5', actual_quantity: null, difference: null, comment: null }] }
const part: InventoryCatalogSearchItem = { id: 31, component_id: 4, component_name: 'Подвязка', name: 'Тяга', article: 'ABC-1', is_active: true, has_photo: false, quantity: '5', minimum_quantity: '2', location: null, stock_is_active: true }
function client(overrides = {}) {
  return {
    inventoryCounts: vi.fn(async () => ({ items: [], limit: 25, offset: 0, total: 0 })),
    searchInventory: vi.fn(async () => ({ items: [part], limit: 25, offset: 0, total: 1 })),
    inventoryCatalogComponents: vi.fn(async () => ({ items: [{ id: 4, name: 'Подвязка', is_active: true, has_photo: false }], limit: 200, offset: 0, total: 1 })),
    createInventoryCount: vi.fn(async () => count),
    updateInventoryCount: vi.fn(async (_park: number, _id: number, lines: InventoryCountLineInput[]): Promise<InventoryCount> => ({ ...count, lines: [{ ...count.lines[0], actual_quantity: lines[0].actual_quantity, difference: '3' }] })),
    postInventoryCount: vi.fn(async () => ({ ...count, status: 'posted' as const })),
    cancelInventoryCount: vi.fn(async () => ({ ...count, status: 'cancelled' as const })),
    ...overrides,
  }
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('shows differences and retains actual quantities beside stale conflicts for retry', async () => {
  const stale = new ApiError(409, { code: 'inventory_count_stale', conflicts: [{ catalog_part_id: 31, expected_quantity: '5', current_quantity: '6', affected_lines: [{ count_line_id: 1, catalog_part_id: 31 }] }] })
  const apiClient = client({ postInventoryCount: vi.fn().mockRejectedValueOnce(stale).mockResolvedValueOnce({ ...count, status: 'posted' }) })
  const user = userEvent.setup()
  render(<InventoryCountsView apiClient={apiClient} parkId={7} permissions={['inventory.stock.manage', 'inventory.documents.post']} />)
  await user.click(await screen.findByRole('button', { name: 'Новая инвентаризация' }))
  await user.type(screen.getByLabelText('Название акта'), 'Сентябрь')
  await user.click(screen.getByRole('button', { name: 'Создать акт' }))
  const actual = await screen.findByRole('textbox', { name: 'Фактически ABC-1' })
  await user.type(actual, '8')
  expect(screen.getByText('Разница: +3')).toBeVisible()
  expect(screen.getByRole('button', { name: 'Сохранить черновик' })).toBeVisible()
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

it('uses response metadata for historical aliases and maps canonical conflicts to them', async () => {
  const aliasCount = { ...count, lines: [{ ...count.lines[0], id: 9, catalog_part_id: 99, catalog_part_article: 'ALIAS-99', catalog_part_name: 'Старая тяга' }] }
  const stale = new ApiError(409, { code: 'inventory_count_stale', conflicts: [{ catalog_part_id: 31, expected_quantity: '5', current_quantity: '6', affected_lines: [{ count_line_id: 9, catalog_part_id: 99 }] }] })
  const searchInventory = vi.fn()
  const apiClient = client({ createInventoryCount: vi.fn(async () => aliasCount), searchInventory, postInventoryCount: vi.fn(async () => { throw stale }) })
  render(<InventoryCountsView apiClient={apiClient} parkId={7} />)
  await userEvent.click(await screen.findByRole('button', { name: 'Новая инвентаризация' })); await userEvent.type(screen.getByLabelText('Название акта'), 'Алиас'); await userEvent.click(screen.getByRole('button', { name: 'Создать акт' }))
  const input = await screen.findByRole('textbox', { name: 'Фактически ALIAS-99' }); await userEvent.type(input, '8')
  await userEvent.click(screen.getByRole('button', { name: 'Провести акт' })); await userEvent.click(await screen.findByRole('button', { name: 'Подтвердить проведение' }))
  expect(await screen.findByText('Ожидалось 5, сейчас 6')).toBeVisible()
  expect(screen.getByText('Старая тяга · ALIAS-99')).toBeVisible()
  expect(searchInventory).not.toHaveBeenCalled()
})

it('loads complete component scope, sends it exactly, and delays validation alerts until touched', async () => {
  const apiClient = client({ inventoryCatalogComponents: vi.fn(async (_park: number, { offset = 0 }: { offset?: number } = {}) => offset === 0 ? { items: [{ id: 4, name: 'Первая', is_active: true, has_photo: false }], limit: 200, offset: 0, total: 2 } : { items: [{ id: 8, name: 'Вторая', is_active: true, has_photo: false }], limit: 200, offset: 1, total: 2 }) })
  render(<InventoryCountsView apiClient={apiClient} parkId={7} permissions={['inventory.stock.manage']} />)
  await userEvent.click(await screen.findByRole('button', { name: 'Новая инвентаризация' }))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  await userEvent.type(screen.getByLabelText('Название акта'), 'Узкий акт')
  await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Охват' }), '8')
  await userEvent.click(screen.getByRole('button', { name: 'Создать акт' }))
  expect(apiClient.createInventoryCount).toHaveBeenCalledWith(7, { name: 'Узкий акт', scope: { kind: 'component', component_id: 8 } })
  expect(screen.queryByRole('button', { name: 'Провести акт' })).not.toBeInTheDocument()
})

it('shows list failures with retry and makes drafts read-only without manage permission', async () => {
  const inventoryCounts = vi.fn().mockRejectedValueOnce(new ApiError(500, null)).mockResolvedValueOnce({ items: [count], limit: 25, offset: 0, total: 1 })
  render(<InventoryCountsView apiClient={client({ inventoryCounts })} parkId={7} permissions={[]} />)
  expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось загрузить акты.')
  await userEvent.click(screen.getByRole('button', { name: 'Повторить загрузку актов' }))
  await userEvent.click(within(await screen.findByRole('article', { name: 'Инвентаризация №71' })).getByRole('button', { name: 'Открыть' }))
  expect(screen.queryByRole('textbox', { name: 'Фактически ABC-1' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Отменить акт' })).not.toBeInTheDocument()
})

it('blocks mobile back and duplicate confirmation while a count is pending', async () => {
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: true, media: '(max-width: 599px)', addEventListener: vi.fn(), removeEventListener: vi.fn() }))
  let resolveUpdate!: (value: InventoryCount) => void
  const updateInventoryCount = vi.fn(() => new Promise<InventoryCount>(resolve => { resolveUpdate = resolve }))
  const apiClient = client({ inventoryCounts: vi.fn(async () => ({ items: [count], limit: 25, offset: 0, total: 1 })), updateInventoryCount })
  render(<InventoryCountsView apiClient={apiClient} parkId={7} />)
  await userEvent.click(within(await screen.findByRole('article', { name: 'Инвентаризация №71' })).getByRole('button', { name: 'Открыть' }))
  await userEvent.type(await screen.findByRole('textbox', { name: 'Фактически ABC-1' }), '8')
  await userEvent.click(screen.getByRole('button', { name: 'Провести акт' }))
  await userEvent.dblClick(await screen.findByRole('button', { name: 'Подтвердить проведение' }))
  expect(updateInventoryCount).toHaveBeenCalledTimes(1)
  expect(screen.getByRole('button', { name: 'Назад к актам' })).toBeDisabled()
  await act(async () => resolveUpdate({ ...count, lines: [{ ...count.lines[0], actual_quantity: '8', difference: '3' }] }))
  await waitFor(() => expect(apiClient.postInventoryCount).toHaveBeenCalledTimes(1))
})

it('supports legacy stale payloads without affected lines', async () => {
  const stale = new ApiError(409, { code: 'inventory_count_stale', conflicts: [{ catalog_part_id: 31, expected_quantity: '5', current_quantity: '6' }] })
  const apiClient = client({ inventoryCounts: vi.fn(async () => ({ items: [{ ...count, lines: [{ ...count.lines[0], actual_quantity: '8' }] }], limit: 25, offset: 0, total: 1 })), postInventoryCount: vi.fn(async () => { throw stale }) })
  render(<InventoryCountsView apiClient={apiClient} parkId={7} permissions={['inventory.documents.post']} />)
  await userEvent.click(within(await screen.findByRole('article', { name: 'Инвентаризация №71' })).getByRole('button', { name: 'Открыть' }))
  expect(screen.getByText('Тяга · ABC-1')).toBeVisible()
  await userEvent.click(screen.getByRole('button', { name: 'Провести акт' }))
  await userEvent.click(await screen.findByRole('button', { name: 'Подтвердить проведение' }))
  expect(await screen.findByText('Ожидалось 5, сейчас 6')).toBeVisible()
})

it('lets manage-only users save count drafts and post-only users post without updates', async () => {
  let resolveSave!: (value: InventoryCount) => void
  const updateInventoryCount = vi.fn(() => new Promise<InventoryCount>(resolve => { resolveSave = resolve }))
  const manageClient = client({ inventoryCounts: vi.fn(async () => ({ items: [count], limit: 25, offset: 0, total: 1 })), updateInventoryCount })
  const manageView = render(<InventoryCountsView apiClient={manageClient} parkId={7} permissions={['inventory.stock.manage']} />)
  await userEvent.click(within(await screen.findByRole('article', { name: 'Инвентаризация №71' })).getByRole('button', { name: 'Открыть' }))
  await userEvent.type(await screen.findByRole('textbox', { name: 'Фактически ABC-1' }), '8')
  await userEvent.dblClick(screen.getByRole('button', { name: 'Сохранить черновик' }))
  expect(manageClient.updateInventoryCount).toHaveBeenCalledTimes(1)
  expect(manageClient.postInventoryCount).not.toHaveBeenCalled()
  await act(async () => resolveSave({ ...count, lines: [{ ...count.lines[0], actual_quantity: '8', difference: '3' }] }))
  await screen.findByText('Черновик сохранён')
  manageView.unmount()

  const postedCount = { ...count, lines: [{ ...count.lines[0], actual_quantity: '8' as const }] }
  const postClient = client({ inventoryCounts: vi.fn(async () => ({ items: [postedCount], limit: 25, offset: 0, total: 1 })) })
  render(<InventoryCountsView apiClient={postClient} parkId={7} permissions={['inventory.documents.post']} />)
  await userEvent.click(within(await screen.findByRole('article', { name: 'Инвентаризация №71' })).getByRole('button', { name: 'Открыть' }))
  await userEvent.click(screen.getByRole('button', { name: 'Провести акт' }))
  await userEvent.click(await screen.findByRole('button', { name: 'Подтвердить проведение' }))
  await waitFor(() => expect(postClient.postInventoryCount).toHaveBeenCalledWith(7, 71))
  expect(postClient.updateInventoryCount).not.toHaveBeenCalled()
})
