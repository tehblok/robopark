import { render, screen, waitFor, within } from '@testing-library/react'
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
    inventoryCatalogComponents: vi.fn(async () => ({ items: [{ id: 4, name: 'Подвязка', is_active: true, has_photo: false }], limit: 200, offset: 0, total: 1 })),
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
})
