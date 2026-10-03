import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiError, type Park, type User } from '../../api'
import { InventoryExportView } from './InventoryExportView'

const next: Park = { id: 1, name: 'Next', timezone: 'Europe/Moscow', tag: 'next', is_active: true }
const archive: Park = { id: 2, name: 'Архив', timezone: 'Europe/Moscow', tag: 'archive', is_active: false }

function renderExport(
  role: User['role'],
  permissions: string[] = ['inventory.export'],
  downloadInventoryExport = vi.fn(async () => undefined),
) {
  render(
    <InventoryExportView
      apiClient={{ downloadInventoryExport }}
      parks={[next, archive]}
      permissions={permissions}
      role={role}
      selectedPark={next}
    />,
  )
  return downloadInventoryExport
}

describe('InventoryExportView', () => {
  it.each(['mechanic', 'operator'] as const)('keeps %s export fixed to the selected park', async role => {
    const download = renderExport(role)

    expect(screen.getByText('Выгрузка парка Next')).toBeVisible()
    expect(screen.queryByRole('option', { name: 'Все парки' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Скачать Excel' }))

    expect(download).toHaveBeenCalledWith({ parkId: 1, format: 'xlsx' })
    expect(await screen.findByRole('status')).toHaveTextContent('Файл Excel скачан')
  })

  it.each(['admin', 'royal'] as const)('lets %s export every park, including inactive parks', async role => {
    const download = renderExport(role)

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Охват выгрузки' }), 'all')
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Формат' }), 'csv')
    await userEvent.click(screen.getByRole('button', { name: 'Скачать CSV' }))

    expect(screen.getByRole('option', { name: 'Архив (неактивен)' })).toBeInTheDocument()
    expect(download).toHaveBeenCalledWith({ scope: 'all', format: 'csv' })
  })

  it('enforces the effective export permission', () => {
    renderExport('admin', [])

    expect(screen.getByRole('alert')).toHaveTextContent('Нет доступа к выгрузке')
    expect(screen.queryByRole('button', { name: /\u0421\u043a\u0430\u0447\u0430\u0442\u044c/ })).not.toBeInTheDocument()
  })

  it('maps server export failures without exposing raw integration values', async () => {
    renderExport('mechanic', ['inventory.export'], vi.fn(async () => {
      throw new ApiError(413, 'inventory_export_too_large')
    }))

    await userEvent.click(screen.getByRole('button', { name: 'Скачать Excel' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Выгрузка слишком большая')
    expect(alert).not.toHaveTextContent('inventory_export_too_large')
  })

  it('ignores completion from an export superseded by a park change', async () => {
    let finish!: () => void
    const pending = new Promise<void>(resolve => { finish = resolve })
    const download = vi.fn(() => pending)
    const view = render(
      <InventoryExportView apiClient={{ downloadInventoryExport: download }} parks={[next, archive]} permissions={['inventory.export']} role="mechanic" selectedPark={next} />,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Скачать Excel' }))

    view.rerender(<InventoryExportView apiClient={{ downloadInventoryExport: download }} parks={[next, archive]} permissions={['inventory.export']} role="mechanic" selectedPark={archive} />)
    finish()

    await waitFor(() => expect(screen.getByText('Выгрузка парка Архив')).toBeVisible())
    expect(screen.queryByText('Файл Excel скачан')).not.toBeInTheDocument()
  })
})
