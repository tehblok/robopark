import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, type InventoryOverview, type Park } from '../../api'
import { ParkScopeContext } from '../../app/park/parkScope'
import { InventoryPage } from './InventoryPage'
import { TaskPartsPanel } from './TaskPartsPanel'

const park: Park = { id: 7, name: 'Север', tag: 'North', is_active: true }
const anotherPark: Park = { id: 8, name: 'Юг', tag: 'South', is_active: true }
const stock: InventoryOverview = { park_id: 7, component_count: 1, part_count: 1, low_stock_count: 0, out_of_stock_count: 0, components: [{ id: 2, park_id: 7, name: 'Подвязка', has_photo: true, parts: [{ id: 3, park_id: 7, component_id: 2, name: 'Тяга', article: 'TY-001', quantity: 5, minimum_quantity: 2, location: 'Стеллаж A / полка 2', is_active: true, has_photo: true }] }] }
const stockWithTwoComponents: InventoryOverview = { ...stock, component_count: 2, part_count: 2, components: [...stock.components, { id: 9, park_id: 7, name: 'Колесо', has_photo: false, parts: [{ id: 10, park_id: 7, component_id: 9, name: 'Шина', article: 'WH-001', quantity: 4, minimum_quantity: 1, location: 'Стеллаж B / полка 1', is_active: true, has_photo: true }] }] }
const stockForAnotherPark: InventoryOverview = { ...stock, park_id: 8, components: [{ id: 12, park_id: 8, name: 'Батарея', has_photo: false, parts: [{ id: 13, park_id: 8, component_id: 12, name: 'Аккумулятор', article: 'BT-001', quantity: 2, minimum_quantity: 1, location: 'Стеллаж C / полка 3', is_active: true, has_photo: false }] }] }
const emptyStock: InventoryOverview = { ...stock, component_count: 0, part_count: 0, low_stock_count: 0, out_of_stock_count: 0, components: [] }

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(currentResolve => { resolve = currentResolve })
  return { promise, resolve }
}

function inventoryClient(overrides = {}) {
  return { ...api, inventory: vi.fn(async () => stock), ...overrides }
}

function renderInventoryPage(currentPark: Park, client = inventoryClient()) {
  return <MemoryRouter><ParkScopeContext.Provider value={{ parkId: currentPark.id, selectedPark: currentPark, parks: [currentPark], loading: false, locked: true, setParkId: vi.fn(), refreshParks: vi.fn() }}><InventoryPage apiClient={client} /></ParkScopeContext.Provider></MemoryRouter>
}

function useViewport(matches: boolean) {
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({
    matches,
    media: '(max-width: 599px)',
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }))
}

beforeEach(() => useViewport(false))

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('keeps the mobile catalog visible and opens inventory workflows on demand', async () => {
  useViewport(true)
  render(renderInventoryPage(park))

  expect(await screen.findByRole('heading', { name: 'Подвязка' })).toBeVisible()
  expect(screen.getByRole('combobox', { name: 'Компонента' })).toBeVisible()
  expect(screen.queryByRole('textbox', { name: 'Название новой запчасти' })).not.toBeInTheDocument()
  expect(screen.queryByRole('spinbutton', { name: 'Количество' })).not.toBeInTheDocument()
  const thumbnail = screen.getByRole('img', { name: 'Тяга' })
  expect(thumbnail).toHaveAttribute('width', '72')
  expect(thumbnail).toHaveAttribute('height', '72')

  await userEvent.click(screen.getByRole('button', { name: 'Добавить запчасть' }))
  expect(screen.getByRole('textbox', { name: 'Название новой запчасти' })).toBeVisible()
})

it('opens a part editor with one disclosure click on a phone', async () => {
  useViewport(true)
  render(renderInventoryPage(park))
  const part = (await screen.findByRole('heading', { name: 'Тяга' })).closest('article')!

  await userEvent.click(within(part).getByRole('button', { name: 'Редактировать' }))

  expect(within(part).getByRole('textbox', { name: 'Название' })).toBeVisible()
  expect(within(part).queryByRole('spinbutton', { name: 'Количество' })).not.toBeInTheDocument()
})

it('opens stock movement with one phone disclosure click and replaces it with editing', async () => {
  useViewport(true)
  render(renderInventoryPage(park))
  const part = (await screen.findByRole('heading', { name: 'Тяга' })).closest('article')!

  await userEvent.click(within(part).getByRole('button', { name: 'Движение остатков' }))
  expect(within(part).getByRole('combobox', { name: 'Операция' })).toBeVisible()
  expect(within(part).getByRole('spinbutton', { name: 'Количество' })).toBeVisible()

  await userEvent.click(within(part).getByRole('button', { name: 'Редактировать' }))
  expect(within(part).getByRole('textbox', { name: 'Название' })).toBeVisible()
  expect(within(part).queryByRole('combobox', { name: 'Операция' })).not.toBeInTheDocument()
})

it('keeps desktop part editors closed and exposes one primary action only for the selected part', async () => {
  render(renderInventoryPage(park, inventoryClient({ inventory: vi.fn(async () => stockWithTwoComponents) })))

  await screen.findByRole('heading', { name: 'Подвязка' })
  const parts = Array.from(document.querySelectorAll<HTMLElement>('.inventory-part'))
  expect(parts).toHaveLength(2)
  expect(document.querySelectorAll('.inventory-parts .rp-button--primary')).toHaveLength(0)
  expect(within(parts[0]).queryByRole('textbox', { name: 'Название' })).not.toBeInTheDocument()
  expect(within(parts[1]).queryByRole('spinbutton', { name: 'Количество' })).not.toBeInTheDocument()

  await userEvent.click(within(parts[0]).getByRole('button', { name: 'Открыть редактор' }))
  expect(within(parts[0]).getByRole('textbox', { name: 'Название' })).toBeVisible()
  expect(within(parts[1]).queryByRole('textbox', { name: 'Название' })).not.toBeInTheDocument()
  expect(document.querySelectorAll('.inventory-parts .rp-button--primary')).toHaveLength(1)
})

it('filters the compact catalog by component without another inventory request', async () => {
  const client = inventoryClient({ inventory: vi.fn(async () => stockWithTwoComponents) })
  render(<MemoryRouter><ParkScopeContext.Provider value={{ parkId: 7, selectedPark: park, parks: [park], loading: false, locked: true, setParkId: vi.fn(), refreshParks: vi.fn() }}><InventoryPage apiClient={client} /></ParkScopeContext.Provider></MemoryRouter>)

  expect(await screen.findByRole('heading', { name: 'Подвязка' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Колесо' })).toBeInTheDocument()
  expect(document.querySelectorAll('img.inventory-part__photo')).toHaveLength(2)

  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Компонента' }), '9')

  expect(screen.queryByRole('heading', { name: 'Подвязка' })).not.toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Колесо' })).toBeInTheDocument()
  expect(client.inventory).toHaveBeenCalledTimes(1)
})

it('resets the component filter when changing parks', async () => {
  const client = inventoryClient({ inventory: vi.fn().mockResolvedValueOnce(stockWithTwoComponents).mockResolvedValueOnce(stockForAnotherPark) })
  const view = render(renderInventoryPage(park, client))

  await screen.findByRole('heading', { name: 'Подвязка' })
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Компонента' }), '9')
  view.rerender(renderInventoryPage(anotherPark, client))

  expect(await screen.findByRole('heading', { name: 'Батарея' })).toBeInTheDocument()
  expect(screen.getByRole('combobox', { name: 'Компонента' })).toHaveValue('all')
})

it('clears stale inventory and ignores an older response after changing parks', async () => {
  const print = vi.spyOn(window, 'print').mockImplementation(() => undefined)
  const initialClient = inventoryClient()
  const view = render(renderInventoryPage(park, initialClient))
  await screen.findByRole('heading', { name: 'Подвязка' })
  await userEvent.click(screen.getByRole('button', { name: 'Печать этикеток' }))
  await waitFor(() => expect(print).toHaveBeenCalledTimes(1))
  expect(document.querySelectorAll('.inventory-print-label')).toHaveLength(1)

  const older = deferred<InventoryOverview>()
  const newer = deferred<InventoryOverview>()
  const reloadingClient = inventoryClient({ inventory: vi.fn((parkId: number) => parkId === 7 ? older.promise : newer.promise) })
  view.rerender(renderInventoryPage(park, reloadingClient))
  await waitFor(() => expect(reloadingClient.inventory).toHaveBeenCalledWith(7))
  view.rerender(renderInventoryPage(anotherPark, reloadingClient))

  await waitFor(() => expect(screen.queryByRole('heading', { name: 'Подвязка' })).not.toBeInTheDocument())
  expect(screen.getByText('Загружаем склад')).toBeVisible()
  expect(document.querySelectorAll('.inventory-print-label')).toHaveLength(0)

  await act(async () => newer.resolve(stockForAnotherPark))
  expect(await screen.findByRole('heading', { name: 'Батарея' })).toBeVisible()
  await act(async () => older.resolve(stock))
  expect(screen.getByRole('heading', { name: 'Батарея' })).toBeVisible()
  expect(screen.queryByRole('heading', { name: 'Подвязка' })).not.toBeInTheDocument()
  print.mockRestore()
})

it('explains when there are no parts to print', async () => {
  render(renderInventoryPage(park, inventoryClient({ inventory: vi.fn(async () => emptyStock) })))
  await screen.findByRole('heading', { name: 'Склад пуст' })
  await userEvent.click(screen.getByRole('button', { name: 'Печать этикеток' }))
  expect(screen.getByRole('alert')).toHaveTextContent('Нет запчастей для печати')
})

it('prints one label for every visible part and excludes filtered components', async () => {
  const print = vi.spyOn(window, 'print').mockImplementation(() => undefined)
  render(<MemoryRouter><ParkScopeContext.Provider value={{ parkId: 7, selectedPark: park, parks: [park], loading: false, locked: true, setParkId: vi.fn(), refreshParks: vi.fn() }}><InventoryPage apiClient={inventoryClient({ inventory: vi.fn(async () => stockWithTwoComponents) })} /></ParkScopeContext.Provider></MemoryRouter>)

  await screen.findByRole('heading', { name: 'Подвязка' })
  await userEvent.click(screen.getByRole('button', { name: 'Печать этикеток' }))

  expect(document.querySelectorAll('.inventory-print-label')).toHaveLength(2)
  expect(document.querySelector('.inventory-print-sheet')).toHaveTextContent('TY-001')
  await waitFor(() => expect(print).toHaveBeenCalledTimes(1))

  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Компонента' }), '9')
  await userEvent.click(screen.getByRole('button', { name: 'Печать этикеток' }))

  expect(document.querySelectorAll('.inventory-print-label')).toHaveLength(1)
  expect(document.querySelector('.inventory-print-sheet')).toHaveTextContent('WH-001')
  await waitFor(() => expect(print).toHaveBeenCalledTimes(2))
})

it('isolates fixed-size printable labels from screen layout geometry', async () => {
  vi.spyOn(window, 'print').mockImplementation(() => undefined)
  render(renderInventoryPage(park, inventoryClient()))

  await screen.findByRole('heading', { name: 'Подвязка' })
  await userEvent.click(screen.getByRole('button', { name: 'Печать этикеток' }))

  const sheet = document.querySelector('.inventory-print-sheet')!
  expect(sheet.parentElement).toBe(document.body)
})

it('shows stock location and prepares a printable shelf label', async () => {
  const print = vi.spyOn(window, 'print').mockImplementation(() => undefined)
  render(<MemoryRouter><ParkScopeContext.Provider value={{ parkId: 7, selectedPark: park, parks: [park], loading: false, locked: true, setParkId: vi.fn(), refreshParks: vi.fn() }}><InventoryPage apiClient={inventoryClient()} /></ParkScopeContext.Provider></MemoryRouter>)
  expect(await screen.findByText('Стеллаж A / полка 2')).toBeVisible()
  await userEvent.click(screen.getByRole('button', { name: 'Распечатать этикетку' }))
  await waitFor(() => expect(print).toHaveBeenCalled())
  const label = document.querySelector('.inventory-print-label')!
  expect(document.querySelectorAll('.inventory-print-label')).toHaveLength(1)
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

it('resets an invalid write-off quantity after inventory refresh', async () => {
  const refreshedStock: InventoryOverview = { ...stock, components: [{ ...stock.components[0], parts: [{ ...stock.components[0].parts[0], quantity: 1 }] }] }
  const client = inventoryClient({ inventory: vi.fn().mockResolvedValueOnce(stock).mockResolvedValueOnce(refreshedStock), writeoffInventoryForTask: vi.fn(async () => ({ id: 1, part_id: 3, park_id: 7, actor_user_id: 4, actor_username: 'mech', kind: 'task_writeoff', delta: -2, balance_after: 1, issue_key: 'RP-42', note: null, created_at: '2026-09-10T10:00:00Z' })) })
  render(<TaskPartsPanel apiClient={client} issueKey="RP-42" parkId={7} />)

  await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Компонента' }), '2')
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Запчасть' }), '3')
  await userEvent.clear(screen.getByRole('spinbutton', { name: 'Списать, шт.' }))
  await userEvent.type(screen.getByRole('spinbutton', { name: 'Списать, шт.' }), '2')
  await userEvent.click(screen.getByRole('button', { name: 'Списать в задачу' }))

  await waitFor(() => expect(client.inventory).toHaveBeenCalledTimes(2))
  expect(screen.getByRole('spinbutton', { name: 'Списать, шт.' })).toHaveValue(1)
})
