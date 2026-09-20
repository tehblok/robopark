import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { NotificationCenter } from './NotificationCenter'

describe('NotificationCenter', () => {
  it('shows the internal inbox even when system notifications are disabled', async () => {
    const markRead = vi.fn(async () => ({ ok: true }))
    render(<NotificationCenter apiClient={{ notificationInbox: async () => [{ id: 'n1', event_type: 'return', park_id: 1, protected_text: 'Задача возвращена', read_at: null, created_at: '2026-09-20T10:00:00Z' }], notificationRead: markRead }} />)
    expect(await screen.findByText('Задача возвращена')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Прочитано' }))
    await waitFor(() => expect(markRead).toHaveBeenCalledWith('n1'))
  })
})
