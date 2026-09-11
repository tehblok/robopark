import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, type InventoryCatalogSearchItem } from '../../api'
import { InventoryManageView } from './InventoryManageView'

const part: InventoryCatalogSearchItem = {
  id: 31, component_id: 4, component_name: 'Подвязка', name: 'Тяга', article: 'ABC-01', is_active: true, has_photo: false,
  quantity: '5', minimum_quantity: '2', location: 'Полка A-1', stock_is_active: true,
}

function client(overrides = {}) {
  return {
    searchInventory: vi.fn(async () => ({ items: [part], limit: 50, offset: 0, total: 1 })),
    inventoryCatalogComponents: vi.fn(async (_parkId: number, _params?: { limit?: number; offset?: number }) => ({ items: [{ id: 4, name: 'Подвязка', is_active: true, has_photo: false }], limit: 200, offset: 0, total: 1 })),
    getInventoryCatalogPart: vi.fn(async () => part),
    createInventoryCatalogComponent: vi.fn(async () => ({ id: 4, name: 'Подвязка', is_active: true, has_photo: false })),
    createInventoryCatalogPart: vi.fn(async () => ({ id: 32, component_id: 4, name: 'Новая тяга', article: 'NEW-01', is_active: true, has_photo: false })),
    updateInventoryCatalogPart: vi.fn(async () => ({ id: 31, component_id: 4, name: 'Тяга', article: 'ABC-01', is_active: true, has_photo: false })),
    mergeInventoryCatalogPart: vi.fn(async () => ({ id: 32, component_id: 4, name: 'Новая тяга', article: 'NEW-01', is_active: true, has_photo: false })),
    updateInventoryStock: vi.fn(async (_parkId: number, catalogPartId: number) => ({ park_id: 1, catalog_part_id: catalogPartId, quantity: '0' as const, minimum_quantity: '0' as const, location: null, is_active: true, version: '1' as const })),
    ...overrides,
  }
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('InventoryManageView', () => {
  it.each(['mechanic', 'operator'] as const)('never exposes global destructive controls to %s', async role => {
    render(<InventoryManageView apiClient={client()} parkId={1} role={role} />)
    await screen.findByRole('option', { name: 'Тяга · ABC-01' })

    expect(screen.queryByRole('button', { name: 'Архивировать глобально' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Объединить глобально' })).not.toBeInTheDocument()
    if (role === 'operator') expect(screen.queryByRole('button', { name: 'Добавить позицию' })).not.toBeInTheDocument()
  })

  it.each(['admin', 'royal'] as const)('lets %s edit, archive and merge a selected global item', async role => {
    const apiClient = client()
    render(<InventoryManageView apiClient={apiClient} parkId={1} role={role} />)
    await screen.findByRole('option', { name: 'Тяга · ABC-01' })
    await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Позиция каталога' }), '31')

    await userEvent.click(screen.getByRole('button', { name: 'Редактировать глобально' }))
    expect(screen.getByRole('form', { name: 'Глобальная позиция' })).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Объединить глобально' }))
    expect(screen.getByRole('form', { name: 'Объединение позиций' })).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Архивировать глобально' }))
    expect(apiClient.updateInventoryCatalogPart).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Подтвердить архивирование' }))
    await waitFor(() => expect(apiClient.updateInventoryCatalogPart).toHaveBeenCalledWith(31, { is_active: false }))
  })

  it('puts mobile global mutations behind one compact action disclosure', async () => {
    vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: true, media: '(max-width: 599px)', onchange: null, addEventListener: vi.fn(), removeEventListener: vi.fn(), addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn() }))
    render(<InventoryManageView apiClient={client()} parkId={1} role="admin" />)
    await screen.findByRole('option', { name: 'Тяга · ABC-01' })
    await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Позиция каталога' }), '31')

    const trigger = screen.getByRole('button', { name: 'Глобальные действия' })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('button', { name: 'Архивировать глобально' })).not.toBeInTheDocument()
    await userEvent.click(trigger)
    expect(screen.getByRole('button', { name: 'Архивировать глобально' })).toBeVisible()
  })

  it('searches and pages catalog sources on the server', async () => {
    const searchInventory = vi.fn(async ({ offset }: { query?: string; offset?: number }) => ({ items: [part], limit: 25, offset: offset ?? 0, total: 60 }))
    const apiClient = client({ searchInventory })
    render(<InventoryManageView apiClient={apiClient} parkId={1} role="admin" />)
    await userEvent.type(await screen.findByRole('searchbox', { name: 'Найти позицию каталога' }), 'ABC')
    await waitFor(() => expect(searchInventory).toHaveBeenCalledWith(expect.objectContaining({ parkId: 1, query: 'ABC', limit: 25, offset: 0 })))
    await userEvent.click(screen.getByRole('button', { name: 'Следующая страница каталога' }))
    await waitFor(() => expect(searchInventory).toHaveBeenLastCalledWith(expect.objectContaining({ query: 'ABC', limit: 25, offset: 25 })))
  })

  it('creates a missing global item and initializes mechanic park stock', async () => {
    const apiClient = client()
    render(<InventoryManageView apiClient={apiClient} parkId={1} role="mechanic" />)
    await screen.findByRole('option', { name: 'Тяга · ABC-01' })
    await userEvent.click(screen.getByRole('button', { name: 'Добавить позицию' }))
    const form = screen.getByRole('form', { name: 'Новая позиция' })
    await userEvent.selectOptions(within(form).getByRole('combobox', { name: 'Компонента' }), '4')
    await userEvent.type(within(form).getByRole('textbox', { name: 'Название' }), 'Новая тяга')
    await userEvent.type(within(form).getByRole('textbox', { name: 'Артикул' }), 'NEW-01')
    await userEvent.type(within(form).getByRole('textbox', { name: 'Место' }), 'Полка B-2')
    await userEvent.clear(within(form).getByRole('textbox', { name: 'Минимум' }))
    await userEvent.type(within(form).getByRole('textbox', { name: 'Минимум' }), '9007199254740993')
    await userEvent.click(within(form).getByRole('button', { name: 'Создать' }))

    await waitFor(() => expect(apiClient.createInventoryCatalogPart).toHaveBeenCalledWith({ park_id: 1, component_id: 4, name: 'Новая тяга', article: 'NEW-01' }))
    expect(apiClient.updateInventoryStock).toHaveBeenCalledWith(1, 32, { minimum_quantity: '9007199254740993', location: 'Полка B-2', is_active: true })
  })

  it('selects a duplicate existing part, opens park settings and preserves the draft', async () => {
    const apiClient = client({
      createInventoryCatalogPart: vi.fn(async () => { throw new ApiError(409, { code: 'inventory_article_exists', existing_part_id: 31 }) }),
    })
    render(<InventoryManageView apiClient={apiClient} parkId={1} role="mechanic" />)
    await screen.findByRole('option', { name: 'Тяга · ABC-01' })
    await userEvent.click(screen.getByRole('button', { name: 'Добавить позицию' }))
    const form = screen.getByRole('form', { name: 'Новая позиция' })
    await userEvent.selectOptions(within(form).getByRole('combobox', { name: 'Компонента' }), '4')
    await userEvent.type(within(form).getByRole('textbox', { name: 'Название' }), 'Дубль')
    await userEvent.type(within(form).getByRole('textbox', { name: 'Артикул' }), 'ABC-01')
    await userEvent.click(within(form).getByRole('button', { name: 'Создать' }))

    expect(await screen.findByRole('form', { name: 'Настройки остатка' })).toBeVisible()
    expect(screen.getByRole('combobox', { name: 'Позиция каталога' })).toHaveValue('31')
    await userEvent.click(screen.getByRole('button', { name: 'Добавить позицию' }))
    expect(screen.getByRole('textbox', { name: 'Название' })).toHaveValue('Дубль')
    expect(screen.getByRole('textbox', { name: 'Артикул' })).toHaveValue('ABC-01')
  })

  it('loads a duplicate that was outside the initial catalog page before opening park settings', async () => {
    const existing = { ...part, id: 99, name: 'Существующая', article: 'HIDDEN-01' }
    const searchInventory = vi.fn()
      .mockResolvedValueOnce({ items: [part], limit: 200, offset: 0, total: 201 })
      .mockResolvedValueOnce({ items: [existing], limit: 25, offset: 0, total: 1 })
    const apiClient = client({
      searchInventory,
      getInventoryCatalogPart: vi.fn(async () => existing),
      createInventoryCatalogPart: vi.fn(async () => { throw new ApiError(409, { code: 'inventory_article_exists', existing_part_id: 99 }) }),
    })
    render(<InventoryManageView apiClient={apiClient} parkId={1} role="mechanic" />)
    await screen.findByRole('option', { name: 'Тяга · ABC-01' })
    await userEvent.click(screen.getByRole('button', { name: 'Добавить позицию' }))
    const form = screen.getByRole('form', { name: 'Новая позиция' })
    await userEvent.selectOptions(within(form).getByRole('combobox', { name: 'Компонента' }), '4')
    await userEvent.type(within(form).getByRole('textbox', { name: 'Название' }), 'Дубль')
    await userEvent.type(within(form).getByRole('textbox', { name: 'Артикул' }), 'HIDDEN-01')
    await userEvent.click(within(form).getByRole('button', { name: 'Создать' }))

    expect(await screen.findByRole('form', { name: 'Настройки остатка' })).toBeVisible()
    expect(apiClient.getInventoryCatalogPart).toHaveBeenCalledWith(1, 99)
    expect(screen.getByRole('combobox', { name: 'Позиция каталога' })).toHaveValue('99')
  })

  it('does not inject duplicate stock fetched for a park that is no longer active', async () => {
    const duplicate = deferred<InventoryCatalogSearchItem>()
    const stale = { ...part, id: 99, location: 'Склад парка A' }
    const apiClient = client({
      searchInventory: vi.fn(async ({ parkId }: { parkId: number }) => ({ items: [{ ...part, location: `Склад парка ${parkId}` }], limit: 25, offset: 0, total: 1 })),
      getInventoryCatalogPart: vi.fn(() => duplicate.promise),
      createInventoryCatalogPart: vi.fn(async () => { throw new ApiError(409, { code: 'inventory_article_exists', existing_part_id: 99 }) }),
    })
    const view = render(<InventoryManageView apiClient={apiClient} parkId={1} role="mechanic" />)
    await screen.findByRole('option', { name: 'Тяга · ABC-01' })
    await userEvent.click(screen.getByRole('button', { name: 'Добавить позицию' }))
    const form = screen.getByRole('form', { name: 'Новая позиция' })
    await userEvent.selectOptions(within(form).getByRole('combobox', { name: 'Компонента' }), '4')
    await userEvent.type(within(form).getByRole('textbox', { name: 'Название' }), 'Дубль')
    await userEvent.type(within(form).getByRole('textbox', { name: 'Артикул' }), 'OLD-01')
    await userEvent.click(within(form).getByRole('button', { name: 'Создать' }))
    await waitFor(() => expect(apiClient.getInventoryCatalogPart).toHaveBeenCalledWith(1, 99))

    view.rerender(<InventoryManageView apiClient={apiClient} parkId={2} role="mechanic" />)
    await act(async () => duplicate.resolve(stale))

    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Позиция каталога' })).toHaveValue(''))
    expect(screen.queryByText('Склад парка A')).not.toBeInTheDocument()
    expect(screen.queryByRole('form', { name: 'Настройки остатка' })).not.toBeInTheDocument()
  })

  it('loads every component page for create forms beyond 200 rows', async () => {
    const components = Array.from({ length: 200 }, (_, index) => ({ id: index + 1, name: `Компонента ${index + 1}`, is_active: true, has_photo: false }))
    const apiClient = client({ inventoryCatalogComponents: vi.fn(async (_parkId: number, { offset = 0 }: { limit?: number; offset?: number } = {}) => offset === 0
      ? { items: components, limit: 200, offset: 0, total: 201 }
      : { items: [{ id: 201, name: 'Последняя компонента', is_active: true, has_photo: false }], limit: 200, offset: 200, total: 201 }) })
    render(<InventoryManageView apiClient={apiClient} parkId={1} role="mechanic" />)
    await userEvent.click(await screen.findByRole('button', { name: 'Добавить позицию' }))

    expect(await within(screen.getByRole('form', { name: 'Новая позиция' })).findByRole('option', { name: 'Последняя компонента' })).toHaveValue('201')
    expect(apiClient.inventoryCatalogComponents).toHaveBeenLastCalledWith(1, { limit: 200, offset: 200 })
  })

  it('ignores stale merge targets after the source query changes', async () => {
    const oldTargets = deferred<{ items: InventoryCatalogSearchItem[]; limit: number; offset: number; total: number }>()
    const newTarget = { ...part, id: 41, name: 'Новая цель', article: 'NEW-TARGET' }
    const oldTarget = { ...part, id: 42, name: 'Старая цель', article: 'OLD-TARGET' }
    const apiClient = client({ searchInventory: vi.fn(({ query }: { query?: string }) => query === 'old'
      ? oldTargets.promise
      : Promise.resolve({ items: query === 'new' ? [newTarget] : [part], limit: 25, offset: 0, total: 1 })) })
    render(<InventoryManageView apiClient={apiClient} parkId={1} role="admin" />)
    await screen.findByRole('option', { name: 'Тяга · ABC-01' })
    await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Позиция каталога' }), '31')
    await userEvent.click(screen.getByRole('button', { name: 'Объединить глобально' }))
    const search = screen.getByRole('searchbox', { name: 'Найти целевую позицию' })
    await userEvent.type(search, 'old')
    await waitFor(() => expect(apiClient.searchInventory).toHaveBeenCalledWith(expect.objectContaining({ query: 'old' })))
    await userEvent.clear(search)
    await userEvent.type(search, 'new')
    expect(await screen.findByRole('option', { name: 'Новая цель · NEW-TARGET' })).toBeVisible()

    await act(async () => oldTargets.resolve({ items: [oldTarget], limit: 25, offset: 0, total: 1 }))
    expect(screen.queryByRole('option', { name: 'Старая цель · OLD-TARGET' })).not.toBeInTheDocument()
  })
})

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(currentResolve => { resolve = currentResolve })
  return { promise, resolve }
}
