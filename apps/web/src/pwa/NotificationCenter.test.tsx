import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { NotificationCenter } from './NotificationCenter'

describe('NotificationCenter', () => {
  afterEach(() => {
    vi.useRealTimers()
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
  })

  it('shows the internal inbox even when system notifications are disabled', async () => {
    const markRead = vi.fn(async () => ({ ok: true }))
    render(<NotificationCenter apiClient={{ notificationInbox: async () => [{ id: 'n1', event_type: 'return', park_id: 1, protected_text: 'Задача возвращена', read_at: null, created_at: new Date().toISOString() }], notificationRead: markRead }} />)
    expect(await screen.findByText('Задача вернулась')).toBeInTheDocument()
    expect(await screen.findByText('Задача возвращена')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Прочитано' }))
    await waitFor(() => expect(markRead).toHaveBeenCalledWith('n1'))
  })

  it('keeps an unread notification when marking it read fails and retries', async () => {
    const markRead = vi.fn()
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({ ok: true })
    render(<NotificationCenter apiClient={{ notificationInbox: async () => [{ id: 'n1', event_type: 'return', park_id: 1, protected_text: 'Задача вернулась', read_at: null, created_at: new Date().toISOString() }], notificationRead: markRead }} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Прочитано' }))

    expect(await screen.findByText('Не удалось отметить уведомление прочитанным.')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Прочитано' }))
    await waitFor(() => expect(markRead).toHaveBeenCalledTimes(2))
    expect(screen.queryByRole('button', { name: 'Прочитано' })).not.toBeInTheDocument()
  })

  it('retries an unavailable internal inbox', async () => {
    const notificationInbox = vi.fn()
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce([{ id: 'n2', event_type: 'report', park_id: 1, protected_text: 'Поступил репорт', read_at: null, created_at: new Date().toISOString() }])
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

  it('removes each notification at its nearest one-day expiry without polling', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-08T12:00:00Z'))
    const notificationInbox = vi.fn(async () => [
      { id: 'soon', event_type: 'return', park_id: 1, protected_text: 'Скоро истечёт', read_at: null, created_at: '2026-10-07T12:00:01Z' },
      { id: 'later', event_type: 'report', park_id: 1, protected_text: 'Остаётся', read_at: null, created_at: '2026-10-08T11:00:00Z' },
    ])
    render(<NotificationCenter apiClient={{ notificationInbox, notificationRead: async () => ({ ok: true }) }} />)
    await act(async () => undefined)

    expect(screen.getByText('Скоро истечёт')).toBeVisible()
    expect(screen.getByText('Остаётся')).toBeVisible()
    await act(async () => { vi.advanceTimersByTime(1_000) })

    expect(screen.queryByText('Скоро истечёт')).not.toBeInTheDocument()
    expect(screen.getByText('Остаётся')).toBeVisible()
    expect(notificationInbox).toHaveBeenCalledTimes(1)
  })

  it('removes notifications that expired while the page was hidden', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-08T12:00:00Z'))
    render(<NotificationCenter apiClient={{
      notificationInbox: async () => [{ id: 'n1', event_type: 'return', park_id: 1, protected_text: 'Истекло в фоне', read_at: null, created_at: '2026-10-07T12:30:00Z' }],
      notificationRead: async () => ({ ok: true }),
    }} />)
    await act(async () => undefined)
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' })
    vi.setSystemTime(new Date('2026-10-08T13:00:00Z'))
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
    fireEvent(document, new Event('visibilitychange'))

    expect(screen.queryByText('Истекло в фоне')).not.toBeInTheDocument()
    expect(screen.getByText('Новых уведомлений нет')).toBeVisible()
  })

  it('does not show a read error when the notification expires during the request', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-08T12:00:00Z'))
    let rejectRead: ((reason?: unknown) => void) | undefined
    const notificationRead = vi.fn(() => new Promise<{ ok: boolean }>((_resolve, reject) => { rejectRead = reject }))
    render(<NotificationCenter apiClient={{
      notificationInbox: async () => [{ id: 'n1', event_type: 'return', park_id: 1, protected_text: 'Истекает', read_at: null, created_at: '2026-10-07T12:00:01Z' }],
      notificationRead,
    }} />)
    await act(async () => undefined)
    fireEvent.click(screen.getByRole('button', { name: 'Прочитано' }))
    await act(async () => {
      vi.advanceTimersByTime(1_000)
      rejectRead?.(new Error('notification_not_found'))
    })

    expect(screen.getByText('Новых уведомлений нет')).toBeVisible()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
