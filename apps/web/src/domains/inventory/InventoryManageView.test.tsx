import { readFileSync } from 'node:fs'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, type InventoryCatalogSearchItem, type InventoryStockView } from '../../api'
import { InventoryManageView } from './InventoryManageView'

const inventoryCss = readFileSync('src/domains/inventory/inventory.css', 'utf8')

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
    replaceInventoryCatalogComponentPhoto: vi.fn(async () => ({ id: 4, name: 'Подвязка', is_active: true, has_photo: true })),
    removeInventoryCatalogComponentPhoto: vi.fn(async () => undefined),
    replaceInventoryCatalogPartPhoto: vi.fn(async () => ({ id: 31, component_id: 4, name: 'Тяга', article: 'ABC-01', is_active: true, has_photo: true })),
    removeInventoryCatalogPartPhoto: vi.fn(async () => undefined),
    permanentlyDeleteInventoryCatalogComponent: vi.fn(async () => ({ deleted_part_count: 1, deleted_part_ids: [31], matched_deleted_count: 1, deleted_parts: [{ id: 31, name: 'Тяга', article: 'ABC-01', is_active: true, component_is_active: true, merged_into_part_id: null }] })),
    permanentlyDeleteInventoryCatalogPart: vi.fn(async (id: number) => ({ deleted_part_count: 1, deleted_part_ids: [id], matched_deleted_count: 1, deleted_parts: [{ id, name: id === 99 ? 'Последняя' : 'Тяга', article: id === 99 ? 'LAST-99' : 'ABC-01', is_active: true, component_is_active: true, merged_into_part_id: null }] })),
    mergeInventoryCatalogPart: vi.fn(async () => ({ id: 32, component_id: 4, name: 'Новая тяга', article: 'NEW-01', is_active: true, has_photo: false })),
    updateInventoryStock: vi.fn(async (_parkId: number, catalogPartId: number) => ({ park_id: 1, catalog_part_id: catalogPartId, quantity: '0' as const, minimum_quantity: '0' as const, location: null, is_active: true, version: '1' as const })),
    ...overrides,
  }
}

function declaredStyles(element: Element): Record<string, string> {
  const style = document.createElement('style')
  style.textContent = inventoryCss
  document.head.append(style)
  const declarations: Record<string, string> = {}
  for (const rule of Array.from((style.sheet as CSSStyleSheet).cssRules)) {
    if (!('selectorText' in rule && 'style' in rule)) continue
    const styleRule = rule as CSSStyleRule
    if (!styleRule.selectorText) continue
    if (!element.matches(styleRule.selectorText)) continue
    for (const property of Array.from(styleRule.style)) declarations[property] = styleRule.style.getPropertyValue(property)
  }
  style.remove()
  return declarations
}

function expectFluidInventoryPhoto(image: HTMLElement) {
  expect(image).toHaveClass('inventory-manage-photo')
  expect(declaredStyles(image)).toMatchObject({
    'block-size': 'auto',
    'max-inline-size': '100%',
    'object-fit': 'contain',
  })
  expect(declaredStyles(image.parentElement!)).toMatchObject({ 'min-inline-size': '0px' })
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('InventoryManageView', () => {
  it('uses semantic mobile stacks and action bars without inline geometry', async () => {
    render(<InventoryManageView apiClient={client()} parkId={1} role="mechanic" />)
    await userEvent.click(await screen.findByRole('button', { name: 'Добавить позицию' }))

    const form = screen.getByRole('form', { name: 'Новая позиция' })
    expect(form.closest('.inventory-manage-view')).toHaveClass('rp-form-stack--mobile')
    expect(form).not.toHaveAttribute('style')
    expect(screen.getByRole('button', { name: 'Добавить позицию' }).parentElement).toHaveClass('rp-action-bar')
  })

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

  it.each(['admin', 'royal'] as const)('lets %s reload, find and restore an archived item', async role => {
    const archived = { ...part, is_active: false }
    const searchInventory = vi.fn(async ({ mode }: { mode?: string }) => ({
      items: mode === 'archived' ? [archived] : [], limit: 25, offset: 0, total: mode === 'archived' ? 1 : 0,
    }))
    const updateInventoryCatalogPart = vi.fn(async () => ({ ...archived, is_active: true }))
    render(<InventoryManageView apiClient={client({ searchInventory, updateInventoryCatalogPart })} parkId={1} role={role} />)

    await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Состояние каталога' }), 'archived')
    await userEvent.type(screen.getByRole('searchbox', { name: 'Найти позицию каталога' }), 'ABC-01')
    await waitFor(() => expect(searchInventory).toHaveBeenCalledWith(expect.objectContaining({ parkId: 1, query: 'ABC-01', mode: 'archived' })))
    await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Позиция каталога' }), '31')
    await userEvent.click(screen.getByRole('button', { name: 'Восстановить глобально' }))

    await waitFor(() => expect(updateInventoryCatalogPart).toHaveBeenCalledWith(31, { is_active: true }))
    await waitFor(() => expect(searchInventory).toHaveBeenLastCalledWith(expect.objectContaining({ mode: 'archived' })))
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
    const reload = vi.fn()
    vi.stubGlobal('location', { ...window.location, reload })
    const savedStock = deferred<InventoryStockView>()
    const apiClient = client({ updateInventoryStock: vi.fn(() => savedStock.promise) })
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
    expect(reload).not.toHaveBeenCalled()
    await act(async () => savedStock.resolve({
      park_id: 1, catalog_part_id: 32, quantity: '0', minimum_quantity: '9007199254740993',
      location: 'Полка B-2', is_active: true, version: '1',
    }))
    expect(await screen.findByRole('form', { name: 'Настройки остатка' })).toBeVisible()
    expect(screen.getByRole('status')).toHaveTextContent('Позиция создана и добавлена в склад парка.')
    expect(reload).not.toHaveBeenCalled()
  })

  it('shows the created component as selected in the sorted cache without reloading the catalog', async () => {
    vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:component-preview'), revokeObjectURL: vi.fn() })
    const apiClient = client({
      createInventoryCatalogComponent: vi.fn(async () => ({ id: 88, name: 'Амортизаторы', is_active: true, has_photo: false })),
      replaceInventoryCatalogComponentPhoto: vi.fn(async () => ({ id: 88, name: 'Амортизаторы', is_active: true, has_photo: true })),
    })
    render(<InventoryManageView apiClient={apiClient} parkId={1} role="mechanic" />)
    await screen.findByRole('option', { name: 'Тяга · ABC-01' })
    await userEvent.click(screen.getByRole('button', { name: 'Добавить компоненту' }))
    const form = screen.getByRole('form', { name: 'Новая компонента' })
    await userEvent.type(within(form).getByRole('textbox', { name: 'Название' }), 'Амортизаторы')
    await userEvent.upload(within(form).getByLabelText('Сделать фото или выбрать файл'), new File(['image'], 'component.jpg', { type: 'image/jpeg' }))
    expectFluidInventoryPhoto(within(form).getByRole('img', { name: 'Предпросмотр фото компоненты' }))
    await userEvent.click(within(form).getByRole('button', { name: 'Создать' }))

    const select = within(await screen.findByRole('form', { name: 'Новая позиция' })).getByRole('combobox', { name: 'Компонента' })
    expect(select).toHaveValue('88')
    expect(select).toHaveDisplayValue('Амортизаторы')
    expect(within(select).getAllByRole('option').map(option => option.textContent)).toEqual(['Выберите', 'Амортизаторы', 'Подвязка'])
    expect(apiClient.searchInventory).toHaveBeenCalledTimes(1)
    expect(apiClient.inventoryCatalogComponents).toHaveBeenCalledTimes(1)
  })

  it.each([false, true])('ignores a component created for an earlier park generation (return to original park: %s)', async returnToOriginalPark => {
    const created = deferred<{ id: number; name: string; is_active: boolean; has_photo: boolean }>()
    const apiClient = client({
      createInventoryCatalogComponent: vi.fn(() => created.promise),
      searchInventory: vi.fn(async ({ parkId }: { parkId: number }) => ({ items: [{ ...part, location: `Парк ${parkId}` }], limit: 25, offset: 0, total: 1 })),
    })
    const view = render(<InventoryManageView apiClient={apiClient} parkId={1} role="mechanic" />)
    await screen.findByRole('option', { name: 'Тяга · ABC-01' })
    await userEvent.click(screen.getByRole('button', { name: 'Добавить компоненту' }))
    const form = screen.getByRole('form', { name: 'Новая компонента' })
    await userEvent.type(within(form).getByRole('textbox', { name: 'Название' }), 'Старая компонента')
    await userEvent.click(within(form).getByRole('button', { name: 'Создать' }))
    await waitFor(() => expect(apiClient.createInventoryCatalogComponent).toHaveBeenCalledWith({ park_id: 1, name: 'Старая компонента' }))

    view.rerender(<InventoryManageView apiClient={apiClient} parkId={2} role="mechanic" />)
    await waitFor(() => expect(apiClient.searchInventory).toHaveBeenLastCalledWith(expect.objectContaining({ parkId: 2 })))
    if (returnToOriginalPark) {
      view.rerender(<InventoryManageView apiClient={apiClient} parkId={1} role="mechanic" />)
      await waitFor(() => expect(apiClient.searchInventory).toHaveBeenLastCalledWith(expect.objectContaining({ parkId: 1 })))
    }
    const searchCount = apiClient.searchInventory.mock.calls.length
    await act(async () => created.resolve({ id: 88, name: 'Старая компонента', is_active: true, has_photo: false }))

    expect(screen.queryByRole('form', { name: 'Новая позиция' })).not.toBeInTheDocument()
    expect(apiClient.searchInventory).toHaveBeenCalledTimes(searchCount)
    await userEvent.click(screen.getByRole('button', { name: 'Добавить позицию' }))
    expect(screen.getByRole('combobox', { name: 'Компонента' })).toHaveValue('')
    expect(screen.queryByRole('option', { name: 'Старая компонента' })).not.toBeInTheDocument()
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

  it('previews and uploads a photo while creating a catalog part', async () => {
    vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:part-preview'), revokeObjectURL: vi.fn() })
    const apiClient = client()
    render(<InventoryManageView apiClient={apiClient} parkId={1} role="mechanic" />)
    await userEvent.click(await screen.findByRole('button', { name: 'Добавить позицию' }))
    const form = screen.getByRole('form', { name: 'Новая позиция' })
    await userEvent.selectOptions(within(form).getByRole('combobox', { name: 'Компонента' }), '4')
    await userEvent.type(within(form).getByRole('textbox', { name: 'Название' }), 'Новая тяга')
    await userEvent.type(within(form).getByRole('textbox', { name: 'Артикул' }), 'NEW-01')
    const photo = new File([new Uint8Array([0xff, 0xd8, 0xff])], 'part.jpg', { type: 'image/jpeg' })
    expect(within(form).getByRole('button', { name: 'Сделать фото или выбрать файл' })).toHaveClass('rp-button--secondary')
    await userEvent.upload(within(form).getByLabelText('Сделать фото или выбрать файл'), photo)
    const preview = within(form).getByRole('img', { name: 'Предпросмотр фото позиции' })
    expect(preview).toHaveAttribute('src', 'blob:part-preview')
    expectFluidInventoryPhoto(preview)
    await userEvent.click(within(form).getByRole('button', { name: 'Создать' }))

    await waitFor(() => expect(apiClient.replaceInventoryCatalogPartPhoto).toHaveBeenCalledWith(32, photo))
    expect(apiClient.searchInventory).toHaveBeenCalledTimes(1)
  })

  it('replaces and removes the current photo in global edit without reloading', async () => {
    vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:replacement'), revokeObjectURL: vi.fn() })
    const photographed = { ...part, has_photo: true }
    const apiClient = client({ searchInventory: vi.fn(async () => ({ items: [photographed], limit: 25, offset: 0, total: 1 })) })
    render(<InventoryManageView apiClient={apiClient} parkId={1} role="royal" />)
    await screen.findByRole('option', { name: 'Тяга · ABC-01' })
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Позиция каталога' }), '31')
    await userEvent.click(screen.getByRole('button', { name: 'Редактировать глобально' }))
    const form = screen.getByRole('form', { name: 'Глобальная позиция' })
    const currentPhoto = within(form).getByRole('img', { name: 'Фото позиции «Тяга»' })
    expect(currentPhoto).toHaveAttribute('src', '/api/inventory/parts/-31/photo')
    expectFluidInventoryPhoto(currentPhoto)
    const replacement = new File([new Uint8Array([0x89, 0x50])], 'part.png', { type: 'image/png' })
    expect(within(form).getByRole('button', { name: 'Заменить' })).toHaveClass('rp-button--secondary')
    await userEvent.upload(within(form).getByLabelText('Заменить'), replacement)
    const preview = within(form).getByRole('img', { name: 'Предпросмотр нового фото' })
    expect(preview).toHaveAttribute('src', 'blob:replacement')
    expectFluidInventoryPhoto(preview)
    await userEvent.click(within(form).getByRole('button', { name: 'Сохранить' }))
    await waitFor(() => expect(apiClient.replaceInventoryCatalogPartPhoto).toHaveBeenCalledWith(31, replacement))
    const deletePhoto = within(form).getByRole('button', { name: 'Удалить' })
    expect(deletePhoto).toHaveClass('rp-button--ghost')
    await userEvent.click(deletePhoto)
    await waitFor(() => expect(apiClient.removeInventoryCatalogPartPhoto).toHaveBeenCalledWith(31))
  })

  it('previews, replaces and removes a component photo without reloading', async () => {
    vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:component-replacement'), revokeObjectURL: vi.fn() })
    const apiClient = client({ inventoryCatalogComponents: vi.fn(async () => ({ items: [{ id: 4, name: 'Подвязка', is_active: true, has_photo: true }], limit: 200, offset: 0, total: 1 })) })
    render(<InventoryManageView apiClient={apiClient} parkId={1} role="admin" />)
    await userEvent.click(await screen.findByRole('button', { name: 'Редактировать компоненту' }))
    const form = screen.getByRole('form', { name: 'Глобальная компонента' })
    await userEvent.selectOptions(within(form).getByRole('combobox', { name: 'Компонента' }), '4')
    const currentPhoto = within(form).getByRole('img', { name: 'Фото компоненты «Подвязка»' })
    expect(currentPhoto).toHaveAttribute('src', '/api/inventory/components/4/photo')
    expectFluidInventoryPhoto(currentPhoto)
    const replacement = new File([new Uint8Array([0x89, 0x50])], 'component.png', { type: 'image/png' })
    expect(within(form).getByRole('button', { name: 'Заменить' })).toHaveClass('rp-button--secondary')
    await userEvent.upload(within(form).getByLabelText('Заменить'), replacement)
    const preview = within(form).getByRole('img', { name: 'Предпросмотр нового фото компоненты' })
    expect(preview).toHaveAttribute('src', 'blob:component-replacement')
    expectFluidInventoryPhoto(preview)
    await userEvent.click(within(form).getByRole('button', { name: 'Сохранить фото' }))
    await waitFor(() => expect(apiClient.replaceInventoryCatalogComponentPhoto).toHaveBeenCalledWith(4, replacement))
    const deletePhoto = within(form).getByRole('button', { name: 'Удалить' })
    expect(deletePhoto).toHaveClass('rp-button--ghost')
    await userEvent.click(deletePhoto)
    await waitFor(() => expect(apiClient.removeInventoryCatalogComponentPhoto).toHaveBeenCalledWith(4))
    expect(apiClient.searchInventory).toHaveBeenCalledTimes(1)
  })

  it('returns to the previous catalog page after deleting its last part', async () => {
    const last = { ...part, id: 99, name: 'Последняя', article: 'LAST-99' }
    const searchInventory = vi.fn(async ({ offset = 0 }: { offset?: number }) => offset === 25
      ? { items: [last], limit: 25, offset: 25, total: 26 }
      : { items: [part], limit: 25, offset: 0, total: 26 })
    const apiClient = client({ searchInventory })
    render(<InventoryManageView apiClient={apiClient} parkId={1} role="royal" />)
    await userEvent.click(await screen.findByRole('button', { name: 'Следующая страница каталога' }))
    await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Позиция каталога' }), '99')
    await userEvent.click(screen.getByRole('button', { name: 'Удалить навсегда' }))
    const dialog = screen.getByRole('alertdialog', { name: 'Удалить позицию «Последняя» навсегда?' })
    await userEvent.type(within(dialog).getByRole('textbox'), 'Последняя')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Удалить навсегда' }))

    await waitFor(() => expect(apiClient.permanentlyDeleteInventoryCatalogPart).toHaveBeenCalledWith(99, { q: undefined, mode: 'active' }))
    expect(searchInventory).toHaveBeenCalledTimes(2)
    expect(screen.getByRole('button', { name: 'Предыдущая страница каталога' })).toBeEnabled()
    expect(screen.getByText('0 из 25')).toBeVisible()
    expect(screen.getByRole('combobox', { name: 'Позиция каталога' })).toHaveValue('')
    await userEvent.click(screen.getByRole('button', { name: 'Предыдущая страница каталога' }))
    await waitFor(() => expect(searchInventory).toHaveBeenCalledTimes(3))
    expect(searchInventory).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 0 }))
    expect(await screen.findByRole('option', { name: 'Тяга · ABC-01' })).toBeVisible()
  })

  it('requires the royal user to type the part name before permanent deletion', async () => {
    const apiClient = client()
    render(<InventoryManageView apiClient={apiClient} parkId={1} role="royal" />)
    await screen.findByRole('option', { name: 'Тяга · ABC-01' })
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Позиция каталога' }), '31')
    await userEvent.click(screen.getByRole('button', { name: 'Удалить навсегда' }))
    const dialog = screen.getByRole('alertdialog', { name: 'Удалить позицию «Тяга» навсегда?' })
    const confirm = within(dialog).getByRole('button', { name: 'Удалить навсегда' })
    expect(confirm).toBeDisabled()
    await userEvent.type(within(dialog).getByRole('textbox'), 'Тяга')
    await userEvent.click(confirm)

    await waitFor(() => expect(apiClient.permanentlyDeleteInventoryCatalogPart).toHaveBeenCalledWith(31, { q: undefined, mode: 'active' }))
    expect(screen.getByRole('combobox', { name: 'Позиция каталога' })).toHaveValue('')
    expect(apiClient.searchInventory).toHaveBeenCalledTimes(1)
  })

  it('permanently deletes a named component only for royal', async () => {
    const apiClient = client({
      searchInventory: vi.fn(async () => ({ items: [part], limit: 25, offset: 0, total: 26 })),
      permanentlyDeleteInventoryCatalogComponent: vi.fn(async () => ({ deleted_part_count: 1, deleted_part_ids: [31], matched_deleted_count: 1, deleted_parts: [{ id: 31, name: 'Тяга', article: 'ABC-01', is_active: true, component_is_active: true, merged_into_part_id: null }] })),
    })
    render(<InventoryManageView apiClient={apiClient} parkId={1} role="royal" />)
    await userEvent.type(await screen.findByRole('searchbox', { name: 'Найти позицию каталога' }), '_')
    await waitFor(() => expect(apiClient.searchInventory).toHaveBeenCalledWith(expect.objectContaining({ query: '_' })))
    const listCallsBeforeDelete = apiClient.searchInventory.mock.calls.length
    await userEvent.click(await screen.findByRole('button', { name: 'Удалить компоненту' }))
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Компонента для удаления' }), '4')
    await userEvent.click(screen.getByRole('button', { name: 'Удалить компоненту навсегда' }))
    const dialog = screen.getByRole('alertdialog', { name: 'Удалить компоненту «Подвязка» навсегда?' })
    await userEvent.type(within(dialog).getByRole('textbox'), 'Подвязка')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Удалить навсегда' }))

    await waitFor(() => expect(apiClient.permanentlyDeleteInventoryCatalogComponent).toHaveBeenCalledWith(4, { q: '_', mode: 'active' }))
    expect(screen.queryByRole('option', { name: 'Подвязка' })).not.toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Страницы каталога' })).not.toBeInTheDocument()
    expect(apiClient.searchInventory).toHaveBeenCalledTimes(listCallsBeforeDelete)
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
