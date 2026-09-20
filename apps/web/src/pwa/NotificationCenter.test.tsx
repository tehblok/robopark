import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { NotificationCenter } from './NotificationCenter'

describe('NotificationCenter', () => {
  it('shows the internal inbox even when system notifications are disabled', async () => {
    const markRead = vi.fn(async () => ({ ok: true }))
    render(<NotificationCenter apiClient={{ notificationInbox: async () => [{ id: 'n1', event_type: 'return', park_id: 1, protected_text: 'Задача возвращена', read_at: null, created_at: '2026-09-20T10:00:00Z' }], notificationRead: markRead }} />)
    expect(await screen.findByText('Задача вернулась')).toBeInTheDocument()
    expect(await screen.findByText('Задача возвращена')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Прочитано' }))
    await waitFor(() => expect(markRead).toHaveBeenCalledWith('n1'))
  })

  it('enables device notifications only after the user presses the button', async () => {
    const pushSubscribe = vi.fn(async () => ({}))
    const subscribe = vi.fn(async () => ({
      endpoint: 'https://push.test/device',
      getKey: () => new Uint8Array([1]).buffer,
    }))
    vi.stubGlobal('Notification', {
      permission: 'default',
      requestPermission: vi.fn(async () => 'granted'),
    })
    vi.stubGlobal('navigator', {
      serviceWorker: { ready: Promise.resolve({ pushManager: { subscribe } }) },
    })
    render(<NotificationCenter apiClient={{
      notificationInbox: async () => [],
      notificationRead: async () => ({ ok: true }),
      pushConfig: async () => ({ public_key: 'BAECAw' }),
      pushSubscribe,
    }} />)

    expect(pushSubscribe).not.toHaveBeenCalled()
    fireEvent.click(await screen.findByRole('button', { name: 'Включить уведомления на устройстве' }))
    await waitFor(() => expect(pushSubscribe).toHaveBeenCalledWith(expect.objectContaining({
      endpoint: 'https://push.test/device',
    })))
    expect(await screen.findByText('Уведомления на устройстве включены.')).toBeInTheDocument()
  })
})
