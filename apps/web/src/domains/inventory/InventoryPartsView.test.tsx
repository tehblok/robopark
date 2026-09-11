import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { InventoryCatalogSearchItem, InventoryPageEnvelope, InventorySearchParams, InventoryStockView } from '../../api'
import { InventoryPartsView } from './InventoryPartsView'

const part: InventoryCatalogSearchItem = {
  id: 31,
  component_id: 4,
  component_name: 'Подвязка',
  name: 'Тяга',
  article: 'ABC-01',
  is_active: true,
  has_photo: true,
  quantity: '5',
  minimum_quantity: '2',
  location: 'Полка A-1',
  stock_is_active: true,
}

const page = (items = [part], overrides: Partial<InventoryPageEnvelope<InventoryCatalogSearchItem>> = {}): InventoryPageEnvelope<InventoryCatalogSearchItem> => ({
  items,
  limit: 25,
  offset: 0,
  total: items.length,
  ...overrides,
})

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(currentResolve => { resolve = currentResolve })
  return { promise, resolve }
}

function client(searchInventory: (params: InventorySearchParams) => Promise<InventoryPageEnvelope<InventoryCatalogSearchItem>> = vi.fn(async () => page())) {
  return {
    searchInventory,
    inventoryCatalogComponents: vi.fn(async (_parkId: number, _params?: { limit?: number; offset?: number }) => ({ items: [{ id: 4, name: 'Подвязка', is_active: true, has_photo: false }], limit: 200, offset: 0, total: 1 })),
    updateInventoryStock: vi.fn(async (_parkId: number, catalogPartId: number): Promise<InventoryStockView> => ({ park_id: 1, catalog_part_id: catalogPartId, quantity: '5', minimum_quantity: '2', location: 'Полка A-1', is_active: true, version: '1' })),
    inventoryPartPhotoUrl: vi.fn((id: number) => `/parts/${id}/photo`),
  }
}

beforeEach(() => {
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({
    matches: false,
    media: '(max-width: 599px)',
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }))
})

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('InventoryPartsView', () => {
  it('debounces search and renders a compact stock card without global controls', async () => {
    const apiClient = client()
    render(<InventoryPartsView apiClient={apiClient} parkId={1} />)

    await userEvent.type(screen.getByRole('searchbox', { name: 'Найти запчасть' }), 'ABC')

    await waitFor(() => expect(apiClient.searchInventory).toHaveBeenLastCalledWith(expect.objectContaining({ parkId: 1, query: 'ABC', limit: 25, offset: 0 })))
    expect(await screen.findByText('Полка A-1')).toBeVisible()
    expect(screen.getByText('5 шт.')).toBeVisible()
    expect(screen.getByRole('img', { name: 'Тяга' })).toHaveAttribute('width', '72')
    expect(screen.getByRole('img', { name: 'Тяга' })).toHaveAttribute('height', '72')
    expect(apiClient.inventoryPartPhotoUrl).toHaveBeenCalledWith(-31)
    expect(screen.queryByRole('button', { name: 'Архивировать глобально' })).not.toBeInTheDocument()
  })

  it('loads component filters independently from the current result page', async () => {
    const apiClient = client(vi.fn(async () => page([])))
    render(<InventoryPartsView apiClient={apiClient} parkId={1} />)
    expect(await screen.findByRole('option', { name: 'Подвязка' })).toHaveValue('4')
    expect(apiClient.inventoryCatalogComponents).toHaveBeenCalledWith(1, { limit: 200, offset: 0 })
  })

  it('loads every component metadata page beyond the first 200 rows', async () => {
    const components = Array.from({ length: 200 }, (_, index) => ({ id: index + 1, name: `Компонента ${index + 1}`, is_active: true, has_photo: false }))
    const apiClient = client(vi.fn(async () => page([])))
    apiClient.inventoryCatalogComponents = vi.fn(async (_parkId: number, { offset = 0 }: { limit?: number; offset?: number } = {}) => offset === 0
      ? { items: components, limit: 200, offset: 0, total: 201 }
      : { items: [{ id: 201, name: 'Последняя компонента', is_active: true, has_photo: false }], limit: 200, offset: 200, total: 201 })
    render(<InventoryPartsView apiClient={apiClient} parkId={1} />)

    expect(await screen.findByRole('option', { name: 'Последняя компонента' })).toHaveValue('201')
    expect(apiClient.inventoryCatalogComponents).toHaveBeenLastCalledWith(1, { limit: 200, offset: 200 })
  })

  it('rejects values above signed int64 without a stock API call', async () => {
    const apiClient = client()
    render(<InventoryPartsView apiClient={apiClient} parkId={1} />)
    const card = (await screen.findByRole('heading', { name: 'Тяга' })).closest('article')!
    await userEvent.click(within(card).getByRole('button', { name: 'Настроить остаток' }))
    const minimum = within(card).getByRole('textbox', { name: 'Минимум' })
    await userEvent.clear(minimum)
    await userEvent.type(minimum, '9223372036854775808')
    expect(within(card).getByText('Не больше 9223372036854775807')).toBeVisible()
    expect(within(card).getByRole('button', { name: 'Сохранить' })).toBeDisabled()
    expect(apiClient.updateInventoryStock).not.toHaveBeenCalled()
  })

  it('ignores a stock response captured for a previous park', async () => {
    const mutation = deferred<InventoryStockView>()
    const searchInventory = vi.fn(({ parkId }: InventorySearchParams) => Promise.resolve(page([{ ...part, location: parkId === 1 ? 'Парк 1' : 'Парк 2' }])))
    const apiClient = client(searchInventory)
    apiClient.updateInventoryStock = vi.fn((_parkId: number, _partId: number) => mutation.promise)
    const view = render(<InventoryPartsView apiClient={apiClient} parkId={1} />)
    const card = (await screen.findByRole('heading', { name: 'Тяга' })).closest('article')!
    await userEvent.click(within(card).getByRole('button', { name: 'Настроить остаток' }))
    await userEvent.click(within(card).getByRole('button', { name: 'Сохранить' }))
    view.rerender(<InventoryPartsView apiClient={apiClient} parkId={2} />)
    expect(await screen.findByText('Парк 2')).toBeVisible()
    await act(async () => mutation.resolve({ park_id: 1, catalog_part_id: 31, quantity: '5', minimum_quantity: '99', location: 'Старый парк', is_active: true, version: '2' }))
    expect(screen.queryByText('Старый парк')).not.toBeInTheDocument()
    expect(screen.getByText('Парк 2')).toBeVisible()
  })

  it('sends filters and pagination while keeping the search field before filters', async () => {
    const searchInventory = vi.fn(async () => page([part], { total: 30 }))
    const apiClient = client(searchInventory)
    render(<InventoryPartsView apiClient={apiClient} parkId={1} />)
    await screen.findByRole('heading', { name: 'Тяга' })

    const search = screen.getByRole('searchbox', { name: 'Найти запчасть' })
    const filters = screen.getByRole('group', { name: 'Фильтры' })
    expect(search.compareDocumentPosition(filters) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Компонента' }), '4')
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Остаток' }), 'in_stock')
    await userEvent.click(screen.getByRole('button', { name: 'Следующая страница' }))

    await waitFor(() => expect(searchInventory).toHaveBeenLastCalledWith({ parkId: 1, query: undefined, componentId: 4, stockFilter: 'in_stock', limit: 25, offset: 25 }))
  })

  it('does not let a slow earlier search replace newer results', async () => {
    const oldRequest = deferred<InventoryPageEnvelope<InventoryCatalogSearchItem>>()
    const newPart = { ...part, id: 32, name: 'Новая тяга', article: 'NEW-01' }
    const searchInventory = vi.fn(({ query }: { query?: string }) => query === 'old' ? oldRequest.promise : Promise.resolve(page(query === 'new' ? [newPart] : [])))
    render(<InventoryPartsView apiClient={client(searchInventory)} debounceMs={10} parkId={1} />)
    const search = screen.getByRole('searchbox', { name: 'Найти запчасть' })
    await userEvent.type(search, 'old')
    await waitFor(() => expect(searchInventory).toHaveBeenCalledWith(expect.objectContaining({ query: 'old' })))
    await userEvent.clear(search)
    await userEvent.type(search, 'new')
    expect(await screen.findByRole('heading', { name: 'Новая тяга' })).toBeVisible()

    await act(async () => oldRequest.resolve(page([part])))

    expect(screen.getByRole('heading', { name: 'Новая тяга' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Тяга' })).not.toBeInTheDocument()
  })

  it('mounts stock or label work for only the selected card', async () => {
    vi.spyOn(window, 'print').mockImplementation(() => undefined)
    vi.mocked(matchMedia).mockReturnValue({ matches: true, media: '(max-width: 599px)', onchange: null, addEventListener: vi.fn(), removeEventListener: vi.fn(), addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn() })
    const second = { ...part, id: 32, name: 'Колесо', article: 'WH-01' }
    render(<InventoryPartsView apiClient={client(vi.fn(async () => page([part, second])))} parkId={1} />)
    const firstCard = (await screen.findByRole('heading', { name: 'Тяга' })).closest('article')!
    const secondCard = screen.getByRole('heading', { name: 'Колесо' }).closest('article')!

    await userEvent.click(within(firstCard).getByRole('button', { name: 'Настроить остаток' }))
    expect(screen.getAllByRole('form', { name: 'Настройки остатка' })).toHaveLength(1)
    await userEvent.click(within(secondCard).getByRole('button', { name: 'Печатать этикетку' }))

    expect(screen.queryByRole('form', { name: 'Настройки остатка' })).not.toBeInTheDocument()
    expect(document.querySelectorAll('.inventory-print-label')).toHaveLength(1)
    expect(document.querySelector('.inventory-print-label')).toHaveTextContent('Колесо')
  })

  it('prints every result on the current filtered page in one desktop action', async () => {
    const print = vi.spyOn(window, 'print').mockImplementation(() => undefined)
    const second = { ...part, id: 32, name: 'Колесо', article: 'WH-01' }
    render(<InventoryPartsView apiClient={client(vi.fn(async () => page([part, second])))} parkId={1} />)
    await screen.findByRole('heading', { name: 'Колесо' })

    await userEvent.click(screen.getByRole('button', { name: 'Выбрать текущую страницу' }))
    await userEvent.click(screen.getByRole('button', { name: 'Печатать выбранные (2)' }))

    expect(document.querySelectorAll('.inventory-print-label')).toHaveLength(2)
    await waitFor(() => expect(print).toHaveBeenCalledTimes(1))
  })

  it('keeps individually chosen labels together on mobile', async () => {
    vi.mocked(matchMedia).mockReturnValue({ matches: true, media: '(max-width: 599px)', onchange: null, addEventListener: vi.fn(), removeEventListener: vi.fn(), addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn() })
    vi.spyOn(window, 'print').mockImplementation(() => undefined)
    const second = { ...part, id: 32, name: 'Колесо', article: 'WH-01' }
    render(<InventoryPartsView apiClient={client(vi.fn(async () => page([part, second])))} parkId={1} />)
    await screen.findByRole('heading', { name: 'Колесо' })

    await userEvent.click(screen.getByRole('checkbox', { name: 'Выбрать для печати Тяга' }))
    await userEvent.click(screen.getByRole('checkbox', { name: 'Выбрать для печати Колесо' }))
    await userEvent.click(screen.getByRole('button', { name: 'Печатать выбранные (2)' }))

    expect(document.querySelectorAll('.inventory-print-label')).toHaveLength(2)
    expect(screen.queryByRole('form', { name: 'Настройки остатка' })).not.toBeInTheDocument()
  })
})
