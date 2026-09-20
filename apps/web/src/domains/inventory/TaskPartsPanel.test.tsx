import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { api, type InventoryCatalogSearchItem } from '../../api'
import { TaskPartsPanel } from './TaskPartsPanel'
import type { OfflineActionInput } from '../../pwa/syncEngine'

const issueParkPart: InventoryCatalogSearchItem = {
  id: 900719925,
  component_id: 22,
  component_name: 'Колёса',
  name: 'Шина',
  article: 'WH-900',
  is_active: true,
  has_photo: false,
  quantity: '9223372036854775807',
  minimum_quantity: '1',
  location: 'Стеллаж issue-park',
  stock_is_active: true,
}

it('uses the issue-park global catalog result and writes off through its negative adapter id', async () => {
  const writeoff = vi.fn(async () => ({
    id: 1, part_id: -issueParkPart.id, catalog_part_id: issueParkPart.id, park_id: 77,
    actor_user_id: 4, actor_username: 'mech', kind: 'task_writeoff', delta: '-9007199254740993' as const,
    balance_after: '9214364837600034814' as const, issue_key: 'RP-77', note: null, created_at: '2026-09-12T10:00:00Z',
  }))
  const searchInventory = vi.fn(async () => ({ items: [issueParkPart], limit: 200, offset: 0, total: 1 }))
  const apiClient = {
    inventory: vi.fn(),
    searchInventory,
    writeoffInventoryForTask: writeoff,
    inventoryComponentPhotoUrl: vi.fn(),
    inventoryPartPhotoUrl: vi.fn(),
  }
  render(<TaskPartsPanel apiClient={apiClient} issueKey="RP-77" parkId={77} />)

  expect(await screen.findByRole('option', { name: 'Колёса' })).toBeInTheDocument()
  expect(screen.getByText(/остаток на складе уменьшится сразу/i)).toBeVisible()
  expect(screen.getByText(/оператор оформит расход в большой системе учёта/i)).toBeVisible()
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Компонента' }), '22')
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Запчасть' }), String(issueParkPart.id))

  expect(screen.getByText('Стеллаж issue-park')).toBeVisible()
  await userEvent.clear(screen.getByRole('textbox', { name: 'Списать, шт.' }))
  await userEvent.type(screen.getByRole('textbox', { name: 'Списать, шт.' }), '9007199254740993')
  await userEvent.click(screen.getByRole('button', { name: 'Списать в задачу' }))

  expect(searchInventory).toHaveBeenCalledWith({ parkId: 77, query: '', limit: 200, offset: 0 })
  await waitFor(() => expect(writeoff).toHaveBeenCalledWith('RP-77', -issueParkPart.id, '9007199254740993', expect.any(String)))
})

it('saves a writeoff locally without waiting for the network and reserves the visible stock', async () => {
  const enqueueAction = vi.fn(async (_input: OfflineActionInput) => undefined)
  const writeoff = vi.fn()
  render(<TaskPartsPanel apiClient={{
    inventory: vi.fn(),
    searchInventory: vi.fn(async () => ({ items: [{ ...issueParkPart, quantity: '2' as const }], limit: 200, offset: 0, total: 1 })),
    writeoffInventoryForTask: writeoff,
    inventoryComponentPhotoUrl: vi.fn(),
    inventoryPartPhotoUrl: vi.fn(),
  }} enqueueAction={enqueueAction} issueKey="RP-OFFLINE" parkId={77} />)
  await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Компонента' }), '22')
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Запчасть' }), String(issueParkPart.id))

  await userEvent.click(screen.getByRole('button', { name: 'Списать в задачу' }))

  await waitFor(() => expect(enqueueAction).toHaveBeenCalledOnce())
  expect(enqueueAction.mock.calls[0]?.[0]).toMatchObject({
    action: 'inventory_writeoff', resourceId: 'RP-OFFLINE',
    payload: { part_id: -issueParkPart.id, quantity: '1', park_id: 77 },
  })
  expect(writeoff).not.toHaveBeenCalled()
  expect(screen.getByRole('status')).toHaveTextContent('Сохранено на устройстве: Шина · 1 шт.')
})

it('reuses the same idempotency key when a writeoff response is retried', async () => {
  const writeoff = vi.fn()
    .mockRejectedValueOnce(new Error('network'))
    .mockResolvedValueOnce({ id: 1 })
  render(<TaskPartsPanel apiClient={{
    inventory: vi.fn(),
    searchInventory: vi.fn(async () => ({ items: [{ ...issueParkPart, quantity: '2' as const }], limit: 200, offset: 0, total: 1 })),
    writeoffInventoryForTask: writeoff,
    inventoryComponentPhotoUrl: vi.fn(),
    inventoryPartPhotoUrl: vi.fn(),
  }} issueKey="RP-RETRY" parkId={77} />)
  await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Компонента' }), '22')
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Запчасть' }), String(issueParkPart.id))

  await userEvent.click(screen.getByRole('button', { name: 'Списать в задачу' }))
  await screen.findByRole('alert')
  await userEvent.click(screen.getByRole('button', { name: 'Списать в задачу' }))

  await waitFor(() => expect(writeoff).toHaveBeenCalledTimes(2))
  expect(writeoff.mock.calls[0][3]).toBe(writeoff.mock.calls[1][3])
})

it('retries only inventory loading when a confirmed writeoff is followed by a reload failure', async () => {
  const writeoff = vi.fn<typeof api.writeoffInventoryForTask>(async () => ({
    id: 1, part_id: -issueParkPart.id, catalog_part_id: issueParkPart.id, park_id: 77,
    actor_user_id: 4, actor_username: 'mech', kind: 'task_writeoff', delta: '-1',
    balance_after: '1', issue_key: 'RP-RELOAD', note: null, created_at: '2026-09-12T10:00:00Z',
  }))
  const searchInventory = vi.fn()
    .mockResolvedValueOnce({ items: [{ ...issueParkPart, quantity: '2' as const }], limit: 200, offset: 0, total: 1 })
    .mockRejectedValueOnce(new Error('reload failed'))
    .mockResolvedValueOnce({ items: [{ ...issueParkPart, quantity: '1' as const }], limit: 200, offset: 0, total: 1 })
  render(<TaskPartsPanel apiClient={{
    inventory: vi.fn(), searchInventory, writeoffInventoryForTask: writeoff,
    inventoryComponentPhotoUrl: vi.fn(), inventoryPartPhotoUrl: vi.fn(),
  }} issueKey="RP-RELOAD" parkId={77} />)
  await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Компонента' }), '22')
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Запчасть' }), String(issueParkPart.id))

  await userEvent.click(screen.getByRole('button', { name: 'Списать в задачу' }))
  await screen.findByRole('alert')
  expect(screen.getByRole('status')).toHaveTextContent('Списано: Шина · 1 шт.')
  expect(screen.getByRole('alert')).toHaveTextContent('Списание повторять не нужно')
  await userEvent.click(screen.getByRole('button', { name: 'Обновить остатки' }))

  await waitFor(() => expect(searchInventory).toHaveBeenCalledTimes(3))
  expect(writeoff).toHaveBeenCalledTimes(1)
})

it('loads the next server page and exposes the 201st issue-park part', async () => {
  const firstPage = Array.from({ length: 200 }, (_, index): InventoryCatalogSearchItem => ({
    ...issueParkPart,
    id: index + 1,
    component_id: 1,
    component_name: 'Первые 200',
    name: `Запчасть ${index + 1}`,
    article: `PART-${index + 1}`,
    quantity: '1',
  }))
  const lastPart = { ...issueParkPart, id: 201, component_id: 2, component_name: 'Дальше', name: 'Запчасть 201', article: 'PART-201', quantity: '3' as const }
  const searchInventory = vi.fn()
    .mockResolvedValueOnce({ items: firstPage, limit: 200, offset: 0, total: 201 })
    .mockResolvedValueOnce({ items: [lastPart], limit: 200, offset: 200, total: 201 })
  render(<TaskPartsPanel apiClient={{
    inventory: vi.fn(), searchInventory, writeoffInventoryForTask: vi.fn(),
    inventoryComponentPhotoUrl: vi.fn(), inventoryPartPhotoUrl: vi.fn(),
  }} issueKey="RP-201" parkId={77} />)

  await userEvent.click(await screen.findByRole('button', { name: 'Загрузить ещё' }))

  expect(await screen.findByRole('option', { name: 'Дальше' })).toBeInTheDocument()
  expect(searchInventory).toHaveBeenNthCalledWith(2, { parkId: 77, query: '', limit: 200, offset: 200 })
  expect(screen.queryByRole('button', { name: 'Загрузить ещё' })).not.toBeInTheDocument()
})

it('does not query another park when the task claim park is unavailable', () => {
  const searchInventory = vi.fn()
  render(<TaskPartsPanel apiClient={{
    inventory: vi.fn(), searchInventory, writeoffInventoryForTask: vi.fn(),
    inventoryComponentPhotoUrl: vi.fn(), inventoryPartPhotoUrl: vi.fn(),
  }} issueKey="RP-NO-PARK" parkId={null} />)

  expect(screen.getByRole('alert')).toHaveTextContent('Парк задачи недоступен')
  expect(searchInventory).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: 'Списать в задачу' })).toBeDisabled()
})

it('finds an absent catalog part by article without allowing a writeoff', async () => {
  const searchInventory = vi.fn(async () => ({ items: [{ ...issueParkPart, quantity: '0' as const, stock_is_active: false }], limit: 200, offset: 0, total: 1 }))
  render(<TaskPartsPanel apiClient={{ inventory: vi.fn(), searchInventory, writeoffInventoryForTask: vi.fn(), inventoryComponentPhotoUrl: vi.fn(), inventoryPartPhotoUrl: vi.fn() }} issueKey="RP-EMPTY" parkId={77} />)
  await screen.findByRole('combobox', { name: 'Компонента' })
  await userEvent.type(screen.getByRole('searchbox', { name: 'Название или артикул' }), 'WH-900')
  await userEvent.click(screen.getByRole('button', { name: 'Найти' }))
  await waitFor(() => expect(searchInventory).toHaveBeenLastCalledWith({ parkId: 77, query: 'WH-900', limit: 200, offset: 0 }))
  await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Компонента' }), '22')
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Запчасть' }), String(issueParkPart.id))
  expect(screen.getByText('Нет на складе')).toBeVisible()
  expect(screen.getByRole('button', { name: 'Списать в задачу' })).toBeDisabled()
})

it('ignores a pending load-more page after the claim park changes', async () => {
  let resolveOldPage!: (value: { items: InventoryCatalogSearchItem[]; limit: number; offset: number; total: number }) => void
  const oldPage = new Promise<{ items: InventoryCatalogSearchItem[]; limit: number; offset: number; total: number }>(resolve => { resolveOldPage = resolve })
  const firstPage = Array.from({ length: 200 }, (_, index) => ({ ...issueParkPart, id: index + 1, quantity: '1' as const }))
  const currentPart = { ...issueParkPart, id: 301, component_id: 30, component_name: 'Парк 88', name: 'Текущая' }
  const stalePart = { ...issueParkPart, id: 201, component_id: 20, component_name: 'Старый парк', name: 'Устаревшая' }
  const searchInventory = vi.fn()
    .mockResolvedValueOnce({ items: firstPage, limit: 200, offset: 0, total: 201 })
    .mockReturnValueOnce(oldPage)
    .mockResolvedValueOnce({ items: [currentPart], limit: 200, offset: 0, total: 1 })
  const apiClient = {
    inventory: vi.fn(), searchInventory, writeoffInventoryForTask: vi.fn(),
    inventoryComponentPhotoUrl: vi.fn(), inventoryPartPhotoUrl: vi.fn(),
  }
  const view = render(<TaskPartsPanel apiClient={apiClient} issueKey="RP-SWITCH" parkId={77} />)
  await userEvent.click(await screen.findByRole('button', { name: 'Загрузить ещё' }))

  view.rerender(<TaskPartsPanel apiClient={apiClient} issueKey="RP-SWITCH" parkId={88} />)
  expect(await screen.findByRole('option', { name: 'Парк 88' })).toBeInTheDocument()
  await act(async () => { resolveOldPage({ items: [stalePart], limit: 200, offset: 200, total: 201 }); await oldPage })

  expect(screen.queryByRole('option', { name: 'Старый парк' })).not.toBeInTheDocument()
})

it('allows only one in-flight writeoff and isolates a different task', async () => {
  let complete!: () => void
  const pending = new Promise<void>(resolve => { complete = resolve })
  const writeoff = vi.fn(async () => { await pending; return { id: 1 } })
  const onWritten = vi.fn()
  const apiClient = { inventory: vi.fn(), searchInventory: vi.fn(async () => ({ items: [issueParkPart], limit: 200, offset: 0, total: 1 })), writeoffInventoryForTask: writeoff as unknown as typeof api.writeoffInventoryForTask, inventoryComponentPhotoUrl: vi.fn(), inventoryPartPhotoUrl: vi.fn() }
  const view = render(<TaskPartsPanel apiClient={apiClient} issueKey="RP-OLD" parkId={77} onWritten={onWritten} />)
  await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Компонента' }), '22')
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Запчасть' }), String(issueParkPart.id))
  const form = screen.getByRole('button', { name: 'Списать в задачу' }).closest('form')!
  fireEvent.submit(form)
  fireEvent.submit(form)
  expect(writeoff).toHaveBeenCalledTimes(1)
  expect(screen.getByRole('combobox', { name: 'Запчасть' })).toBeDisabled()
  view.rerender(<TaskPartsPanel apiClient={apiClient} issueKey="RP-NEW" parkId={77} onWritten={onWritten} />)
  expect(await screen.findByRole('combobox', { name: 'Компонента' })).toHaveValue('')
  await act(async () => { complete(); await pending })
  expect(onWritten).not.toHaveBeenCalled()
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
})
