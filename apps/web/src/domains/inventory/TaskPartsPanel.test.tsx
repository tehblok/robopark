import { render, screen, waitFor } from '@testing-library/react'
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

  expect(searchInventory).toHaveBeenCalledWith({ parkId: 77, limit: 200, offset: 0 })
  await waitFor(() => expect(writeoff).toHaveBeenCalledWith('RP-77', -issueParkPart.id, '9007199254740993'))
})
