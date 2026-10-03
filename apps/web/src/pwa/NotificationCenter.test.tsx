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

  it('keeps an unread notification when marking it read fails and retries', async () => {
    const markRead = vi.fn()
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({ ok: true })
    render(<NotificationCenter apiClient={{ notificationInbox: async () => [{ id: 'n1', event_type: 'return', park_id: 1, protected_text: 'Задача вернулась', read_at: null, created_at: '2026-09-20T10:00:00Z' }], notificationRead: markRead }} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Прочитано' }))

    expect(await screen.findByText('Не удалось отметить уведомление прочитанным.')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Прочитано' }))
    await waitFor(() => expect(markRead).toHaveBeenCalledTimes(2))
    expect(screen.queryByRole('button', { name: 'Прочитано' })).not.toBeInTheDocument()
  })

  it('retries an unavailable internal inbox', async () => {
    const notificationInbox = vi.fn()
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce([{ id: 'n2', event_type: 'report', park_id: 1, protected_text: 'Поступил репорт', read_at: null, created_at: '2026-09-20T10:00:00Z' }])
    render(<NotificationCenter apiClient={{ notificationInbox, notificationRead: async () => ({ ok: true }) }} />)

    expect(await screen.findByText('Не удалось загрузить уведомления')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Повторить' }))
    expect(await screen.findByText('Поступил репорт')).toBeVisible()
    expect(notificationInbox).toHaveBeenCalledTimes(2)
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
