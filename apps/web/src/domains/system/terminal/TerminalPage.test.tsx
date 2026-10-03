import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { ApiError } from '../../../api'
import { TerminalPage } from './TerminalPage'
import type { SessionOut, TerminalApiClient } from './terminalApi'
import type { TerminalConnection } from './terminalTransport'

vi.mock('./TerminalViewport', () => ({
  TerminalViewport: ({ profile }: { profile: string }) => <div data-testid={`viewport-${profile}`}>{profile}</div>,
}))

const maintenance: SessionOut = { id: '11111111-1111-4111-8111-111111111111', profile: 'maintenance', state: 'active', expires_at: '2026-10-02T13:00:00Z', broker_epoch: 'epoch', termination_reason: null }
const root: SessionOut = { ...maintenance, id: '22222222-2222-4222-8222-222222222222', profile: 'root', expires_at: '2026-10-02T12:15:00Z' }
const connectionFactory = () => ({ connect: vi.fn().mockResolvedValue(undefined), close: vi.fn(), sendInput: vi.fn(), resize: vi.fn() } as unknown as TerminalConnection)

function client(overrides: Partial<TerminalApiClient> = {}): TerminalApiClient {
  return {
    capabilities: vi.fn().mockResolvedValue({ available: true, boot_id: 'boot', broker_epoch: 'epoch', capability_revision: 'rev', profiles: ['maintenance', 'root'], active_sessions: 0 }),
    reauthorize: vi.fn().mockResolvedValue({ token: 'grant', expires_in: 30 }),
    create: vi.fn().mockImplementation(({ profile }) => Promise.resolve(profile === 'root' ? root : maintenance)),
    list: vi.fn().mockResolvedValue([]), attachTicket: vi.fn().mockResolvedValue({ ticket: 'ticket', expires_in: 15 }),
    terminate: vi.fn().mockImplementation((id) => Promise.resolve({ ...(id === root.id ? root : maintenance), state: 'ended' })),
    ...overrides,
  }
}

async function authorize(profile: 'maintenance' | 'root') {
  fireEvent.click(screen.getByRole('button', { name: profile === 'root' ? 'Открыть root' : 'Открыть терминал' }))
  fireEvent.change(screen.getByLabelText('Пароль'), { target: { value: 'secret' } })
  fireEvent.change(screen.getByLabelText('Свежий код TOTP'), { target: { value: '123456' } })
  fireEvent.click(screen.getByRole('button', { name: 'Подтвердить и открыть' }))
}

it('opens root as a separately confirmed session while maintenance stays mounted', async () => {
  const api = client()
  render(<TerminalPage apiClient={api} connectionFactory={connectionFactory} loadCurrentUser={async () => ({ id: 7, username: 'owner', role: 'royal', access_status: 'approved' })} />)
  await screen.findByText('Терминал хоста')
  await authorize('maintenance')
  expect(await screen.findByTestId('viewport-maintenance')).toBeVisible()
  await authorize('root')
  expect(await screen.findByTestId('viewport-root')).toBeVisible()
  expect(screen.getByTestId('viewport-maintenance')).toBeVisible()
  expect(api.reauthorize).toHaveBeenNthCalledWith(2, expect.objectContaining({ operation_kind: 'terminal.open.root' }))
  expect(screen.getByText(/root · завершится/)).toBeVisible()
})

it('keeps the login active after invalid TOTP and clears both secret fields', async () => {
  const api = client({ reauthorize: vi.fn().mockRejectedValue(new ApiError(401, 'invalid_totp')) })
  const loadCurrentUser = vi.fn().mockResolvedValue({ id: 7, username: 'owner', role: 'royal', access_status: 'approved' })
  render(<TerminalPage apiClient={api} connectionFactory={connectionFactory} loadCurrentUser={loadCurrentUser} />)
  await screen.findByText('Терминал хоста')
  await authorize('maintenance')
  expect(await screen.findByText('Неверный пароль или код TOTP. Вход на сайт сохранён.')).toBeVisible()
  expect(screen.getByLabelText('Пароль')).toHaveValue('')
  expect(screen.getByLabelText('Свежий код TOTP')).toHaveValue('')
  expect(loadCurrentUser).toHaveBeenCalledTimes(1)
})

it('clears terminal output when online identity changes or is revoked', async () => {
  const api = client({ list: vi.fn().mockResolvedValue([maintenance]) })
  const loadCurrentUser = vi.fn()
    .mockResolvedValueOnce({ id: 7, username: 'owner', role: 'royal', access_status: 'approved' })
    .mockRejectedValueOnce(new ApiError(401, null))
  render(<TerminalPage apiClient={api} connectionFactory={connectionFactory} loadCurrentUser={loadCurrentUser} identityPollMs={25} />)
  expect(await screen.findByTestId('viewport-maintenance')).toBeVisible()
  await waitFor(() => expect(screen.queryByTestId('viewport-maintenance')).toBeNull())
  expect(screen.getByText('Сессия входа завершена. Вернитесь на сайт и войдите снова.')).toBeVisible()
})

it('releases the profile action when the broker ends a session naturally', async () => {
  let end: ((reason?: string) => void) | undefined
  const makeConnection = (_session: SessionOut, callbacks: { onEnded: (reason?: string) => void }) => {
    end = callbacks.onEnded
    return { connect: vi.fn().mockResolvedValue(undefined), close: vi.fn(), sendInput: vi.fn(), resize: vi.fn() } as unknown as TerminalConnection
  }
  const api = client({ list: vi.fn().mockResolvedValue([maintenance]) })
  render(<TerminalPage apiClient={api} connectionFactory={makeConnection} loadCurrentUser={async () => ({ id: 7, username: 'owner', role: 'royal', access_status: 'approved' })} />)

  expect(await screen.findByTestId('viewport-maintenance')).toBeVisible()
  expect(screen.getByRole('button', { name: 'Открыть терминал' })).toBeDisabled()
  end?.('expired')

  await waitFor(() => expect(screen.getByRole('button', { name: 'Открыть терминал' })).toBeEnabled())
  expect(screen.getByText('Сессия завершена: expired.')).toBeVisible()
})
