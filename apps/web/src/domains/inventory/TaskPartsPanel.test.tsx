import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import type { InventoryCatalogSearchItem } from '../../api'
import { TaskPartsPanel } from './TaskPartsPanel'

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
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Компонента' }), '22')
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Запчасть' }), String(issueParkPart.id))

  expect(screen.getByText('Стеллаж issue-park')).toBeVisible()
  await userEvent.clear(screen.getByRole('textbox', { name: 'Списать, шт.' }))
  await userEvent.type(screen.getByRole('textbox', { name: 'Списать, шт.' }), '9007199254740993')
  await userEvent.click(screen.getByRole('button', { name: 'Списать в задачу' }))

  expect(searchInventory).toHaveBeenCalledWith({ parkId: 77, stockFilter: 'in_stock', limit: 200, offset: 0 })
  await waitFor(() => expect(writeoff).toHaveBeenCalledWith('RP-77', -issueParkPart.id, '9007199254740993'))
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
  expect(searchInventory).toHaveBeenNthCalledWith(2, { parkId: 77, stockFilter: 'in_stock', limit: 200, offset: 200 })
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
