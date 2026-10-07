import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { TelegramAccessPanel } from './TelegramAccessPanel'

describe('TelegramAccessPanel', () => {
  it('creates the same park request and refreshes approval without duplicate requests', async () => {
    const empty = { access_status: 'pending', available_parks: [{ id: 3, name: 'Северный' }], assigned_parks: [], requests: [] }
    const row = { id: 1, status: 'pending', park: empty.available_parks[0] }
    const client = { get: vi.fn().mockResolvedValueOnce(empty).mockResolvedValue({ ...empty, requests: [row] }), create: vi.fn().mockResolvedValue(row) }
    const refresh = vi.fn().mockResolvedValue(undefined)
    render(<TelegramAccessPanel client={client} onRefresh={refresh} />)
    fireEvent.change(await screen.findByLabelText('Парк для доступа'), { target: { value: '3' } })
    fireEvent.click(screen.getByRole('button', { name: 'Запросить доступ' }))
    await screen.findByText('Северный: ожидает одобрения')
    expect(client.create).toHaveBeenCalledWith(3)
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('button', { name: 'Запросить доступ' })).not.toBeInTheDocument()
  })

  it('allows retry when the first load fails', async () => {
    const client = { get: vi.fn().mockRejectedValueOnce(new Error('network')).mockResolvedValue({ access_status: 'pending', available_parks: [], assigned_parks: [], requests: [] }), create: vi.fn() }
    render(<TelegramAccessPanel client={client} onRefresh={vi.fn()} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось загрузить')
    fireEvent.click(screen.getByRole('button', { name: 'Проверить одобрение' }))
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })
})
