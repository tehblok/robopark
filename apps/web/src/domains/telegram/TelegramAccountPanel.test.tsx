import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { TelegramAccountPanel } from './TelegramAccountPanel'
import type { NativeTelegramClient } from './nativeTelegramApi'

function client(): NativeTelegramClient {
  return {
    getAdmin: vi.fn(), updatePark: vi.fn(), createJob: vi.fn(), updateJob: vi.fn(), deleteJob: vi.fn(), runJob: vi.fn(),
    getAccount: vi.fn().mockResolvedValue({ linked: false, telegram_user_id: null }),
    createLinkCode: vi.fn().mockResolvedValue({ code: 'ABCD12', expires_at: '2026-10-07T10:00:00Z' }),
    unlinkAccount: vi.fn().mockResolvedValue(undefined),
    getMigrationPreview: vi.fn(), applyMigration: vi.fn(),
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(next => { resolve = next })
  return { promise, resolve }
}

describe('TelegramAccountPanel', () => {
  it('shows a link code only after explicit action and explains the private-chat command', async () => {
    const api = client()
    render(<TelegramAccountPanel client={api} />)
    expect(await screen.findByText('Telegram не привязан')).toBeVisible()
    expect(screen.queryByText('ABCD12')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Получить код привязки' }))
    expect(await screen.findByText('ABCD12')).toBeVisible()
    expect(screen.getByText((_, node) => node?.tagName === 'P' && node.textContent?.includes('/link ABCD12') === true)).toHaveTextContent('в личном чате')
  })

  it('revokes only the current account through the endpoint without a user id', async () => {
    const api = client()
    api.getAccount = vi.fn().mockResolvedValue({ linked: true, telegram_user_id: 123456 })
    render(<TelegramAccountPanel client={api} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Отвязать Telegram' }))
    await waitFor(() => expect(api.unlinkAccount).toHaveBeenCalledWith())
    expect(await screen.findByText('Telegram не привязан')).toBeVisible()
  })

  it('ignores a link code returned after the account client changes', async () => {
    const oldClient = client()
    const lateCode = deferred<{ code: string; expires_at: string }>()
    oldClient.createLinkCode = vi.fn().mockReturnValue(lateCode.promise)
    const view = render(<TelegramAccountPanel client={oldClient} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Получить код привязки' }))

    const newClient = client()
    view.rerender(<TelegramAccountPanel client={newClient} />)
    expect(await screen.findByText('Telegram не привязан')).toBeVisible()
    await act(async () => lateCode.resolve({ code: 'STALE1', expires_at: '2026-10-07T10:00:00Z' }))

    expect(screen.queryByText('STALE1')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Получить код привязки' })).toBeEnabled()
  })
})
