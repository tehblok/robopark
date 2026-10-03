import { useState } from 'react'
import { IDBFactory } from 'fake-indexeddb'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type User } from '../api'
import { AuthContext } from '../auth-context'
import { ParkScopeContext } from '../app/park/parkScope'
import { offlineScopeForUser } from '../lib/deviceResourceCache'
import { purgeOfflineScope } from './offlineDb'
import { SyncProvider, type SyncEngineLike } from './SyncProvider'
import { useSync } from './syncContext'
import { registerServiceWorker, setServiceWorkerAuthState, setServiceWorkerSyncState } from './registerServiceWorker'

const user: User = { id: 1, username: 'mech', role: 'mechanic', access_status: 'approved', permissions: ['tracker.read'], parks: [] }

function Probe() {
  const { state } = useSync()
  return <span>{state.status}</span>
}

describe('SyncProvider', () => {
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); setServiceWorkerAuthState(null); setServiceWorkerSyncState(null) })

  it('does not expose the previous park engine while the new park opens', async () => {
    let resolveNext!: (engine: SyncEngineLike) => void
    const next = new Promise<SyncEngineLike>(resolve => { resolveNext = resolve })
    const first = { start: vi.fn(), dispose: vi.fn(), subscribe: vi.fn(() => () => undefined), getState: () => ({ status: 'idle' as const, pending: 0, conflicts: 0 }), enqueueAction: vi.fn(async () => undefined) } satisfies SyncEngineLike
    const second = { ...first, start: vi.fn(), dispose: vi.fn(), enqueueAction: vi.fn(async () => undefined) }
    const factory = vi.fn().mockResolvedValueOnce(first).mockReturnValueOnce(next)
    const auth = { user, loading: false, login: vi.fn(), logout: vi.fn(), refreshUser: vi.fn() }
    const park = (parkId: number) => ({ parkId, selectedPark: null, parks: [], loading: false, locked: false, setParkId: vi.fn(), refreshParks: vi.fn() })
    function ScopeProbe() {
      const sync = useSync()
      return <button disabled={!sync.actionTrackingReady} onClick={() => { void sync.enqueueAction({ id: 'scope-test', deviceId: 'phone', resourceType: 'schedule', resourceId: 'scope-test', action: 'schedule_create', idempotencyKey: 'scope-test', baseRevision: null, dependencies: [], payload: { park_id: 2 } }) }}>{sync.scopeKey ?? 'unready'}</button>
    }
    const view = render(<AuthContext.Provider value={auth}><ParkScopeContext.Provider value={park(1)}><SyncProvider engineFactory={factory}><ScopeProbe /></SyncProvider></ParkScopeContext.Provider></AuthContext.Provider>)
    const button = screen.getByRole('button')
    await waitFor(() => expect(button).toBeEnabled())
    expect(button).toHaveTextContent(JSON.stringify(offlineScopeForUser(user, '1')))
    view.rerender(<AuthContext.Provider value={auth}><ParkScopeContext.Provider value={park(2)}><SyncProvider engineFactory={factory}><ScopeProbe /></SyncProvider></ParkScopeContext.Provider></AuthContext.Provider>)
    expect(button).toBeDisabled()
    resolveNext(second)
    await waitFor(() => expect(button).toBeEnabled())
    expect(button).toHaveTextContent(JSON.stringify(offlineScopeForUser(user, '2')))
  })

  it('keeps a newly clicked action out of the fallback queue while activation is prepared', async () => {
    const listeners = new Map<string, (event: { data?: unknown, ports?: { postMessage: (value: unknown) => void, onmessage?: (event: { data?: unknown }) => void }[] }) => void>()
    await registerServiceWorker({ production: true, secure: true, serviceWorker: {
      register: vi.fn(async () => ({ update: vi.fn(async () => undefined) })),
      addEventListener: (name, listener) => listeners.set(name, listener),
    } })
    setServiceWorkerAuthState({ loading: false, accountId: user.id })
    const enqueueAction = vi.fn(async () => undefined)
    const engine = { start: vi.fn(), dispose: vi.fn(), subscribe: vi.fn(() => () => undefined), getState: () => ({ status: 'idle' as const, pending: 0, conflicts: 0 }), enqueueAction } satisfies SyncEngineLike
    const auth = { user, loading: false, login: vi.fn(), logout: vi.fn(), refreshUser: vi.fn() }
    const park = { parkId: 1, selectedPark: null, parks: [], loading: false, locked: false, setParkId: vi.fn(), refreshParks: vi.fn() }
    function ActionProbe() {
      const sync = useSync()
      const [error, setError] = useState('')
      return <><button disabled={!sync.actionTrackingReady} onClick={() => {
        void sync.enqueueAction({ id: 'during-update', deviceId: 'phone', resourceType: 'tracker_issue', resourceId: 'TASK-1', action: 'comment', idempotencyKey: 'during-update', baseRevision: null, dependencies: [], payload: { text: 'keep this' } }).catch(reason => setError((reason as Error).message))
      }}>Send</button><span>{error}</span></>
    }
    render(<AuthContext.Provider value={auth}><ParkScopeContext.Provider value={park}><SyncProvider engineFactory={async () => engine}><ActionProbe /></SyncProvider></ParkScopeContext.Provider></AuthContext.Provider>)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled())
    const replies: unknown[] = []
    const port = { postMessage: (value: unknown) => replies.push(value), onmessage: undefined as undefined | ((event: { data?: unknown }) => void) }
    listeners.get('message')?.({ data: { type: 'PREPARE_ACTIVATION' }, ports: [port] })
    expect(replies).toEqual([expect.objectContaining({ safe: true })])
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByText('pwa_update_in_progress')).toBeInTheDocument()
    expect(enqueueAction).not.toHaveBeenCalled()
    port.onmessage?.({ data: { type: 'RELEASE_ACTIVATION' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() => expect(enqueueAction).toHaveBeenCalledOnce())
  })

  it('uses direct network delivery when IndexedDB is denied', async () => {
    vi.stubGlobal('indexedDB', undefined)
    const send = vi.spyOn(api, 'syncBatch').mockImplementation(async batch => ({
      results: batch.actions.map(item => ({ client_action_id: item.client_action_id, state: 'confirmed' as const, code: null, result: null })),
      deltas: {}, revisions: {}, revoked_scopes: [],
    }))
    const auth = { user, loading: false, login: vi.fn(), logout: vi.fn(), refreshUser: vi.fn() }
    const park = { parkId: 1, selectedPark: null, parks: [], loading: false, locked: false, setParkId: vi.fn(), refreshParks: vi.fn() }
    function ActionProbe() {
      const sync = useSync()
      return <button disabled={!sync.actionTrackingReady} onClick={() => { void sync.enqueueAction({ id: 'direct', deviceId: 'phone', resourceType: 'tracker_issue', resourceId: 'TASK-1', action: 'comment', idempotencyKey: 'direct', baseRevision: null, dependencies: [], payload: { text: 'hello' } }) }}>Send</button>
    }
    render(<AuthContext.Provider value={auth}><ParkScopeContext.Provider value={park}><SyncProvider><ActionProbe /></SyncProvider></ParkScopeContext.Provider></AuthContext.Provider>)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() => expect(send).toHaveBeenCalledOnce())
    expect(send.mock.calls[0][0].actions[0].client_action_id).toBe('direct')
  })

  it('retires direct network delivery when the server revokes park access', async () => {
    vi.stubGlobal('indexedDB', undefined)
    const scopedUser: User = { ...user, parks: [{ id: 1, name: 'Парк', tag: 'park', timezone: 'Europe/Moscow' }] }
    vi.spyOn(api, 'syncBatch').mockResolvedValue({
      results: [{ client_action_id: 'direct-revoked', state: 'rejected', code: 'park_forbidden', result: null }],
      deltas: {}, revisions: {}, revoked_scopes: ['work:park:1'],
    })
    const refreshUser = vi.fn(async () => ({ ...scopedUser, parks: [] }))
    const auth = { user: scopedUser, loading: false, login: vi.fn(), logout: vi.fn(), refreshUser }
    const park = { parkId: 1, selectedPark: null, parks: [], loading: false, locked: false, setParkId: vi.fn(), refreshParks: vi.fn() }
    function ActionProbe() {
      const sync = useSync()
      return <button disabled={!sync.actionTrackingReady} onClick={() => {
        void sync.enqueueAction({ id: 'direct-revoked', deviceId: 'phone', resourceType: 'tracker_issue', resourceId: 'TASK-1', action: 'comment', idempotencyKey: 'direct-revoked-key', baseRevision: null, dependencies: [], payload: { park_id: 1, text: 'draft' } }).catch(() => undefined)
      }}>Send</button>
    }
    const view = render(<AuthContext.Provider value={auth}><ParkScopeContext.Provider value={park}><SyncProvider><ActionProbe /></SyncProvider></ParkScopeContext.Provider></AuthContext.Provider>)
    try {
      const button = screen.getByRole('button', { name: 'Send' })
      await waitFor(() => expect(button).toBeEnabled())
      fireEvent.click(button)
      await waitFor(() => expect(refreshUser).toHaveBeenCalledOnce())
      expect(button).toBeDisabled()
    } finally {
      view.unmount()
      await purgeOfflineScope()
    }
  })

  it('stops exposing the old offline scope after the server revokes its park', async () => {
    vi.stubGlobal('indexedDB', new IDBFactory())
    const scopedUser: User = { ...user, parks: [{ id: 1, name: 'Парк', tag: 'park', timezone: 'Europe/Moscow' }] }
    vi.spyOn(api, 'syncBatch').mockResolvedValue({
      results: [{ client_action_id: 'revoked-action', state: 'rejected', code: 'park_forbidden', result: null }],
      deltas: {}, revisions: {}, revoked_scopes: ['work:park:1'],
    })
    const refreshUser = vi.fn(async () => ({ ...scopedUser, parks: [] }))
    const auth = { user: scopedUser, loading: false, login: vi.fn(), logout: vi.fn(), refreshUser }
    const authorizationFailure = vi.fn()
    window.addEventListener('robopark:authorization-failure', authorizationFailure)
    const park = { parkId: 1, selectedPark: null, parks: [], loading: false, locked: false, setParkId: vi.fn(), refreshParks: vi.fn() }
    function ActionProbe() {
      const sync = useSync()
      return <button disabled={!sync.actionTrackingReady} onClick={() => {
        void sync.enqueueAction({ id: 'revoked-action', deviceId: 'phone', resourceType: 'tracker_issue', resourceId: 'TASK-1', action: 'comment', idempotencyKey: 'revoked-action-key', baseRevision: null, dependencies: [], payload: { park_id: 1, text: 'draft' } })
          .then(() => sync.syncNow('test'))
      }}>Send</button>
    }
    const view = render(<AuthContext.Provider value={auth}><ParkScopeContext.Provider value={park}><SyncProvider><ActionProbe /></SyncProvider></ParkScopeContext.Provider></AuthContext.Provider>)
    try {
      const button = await screen.findByRole('button', { name: 'Send' })
      await waitFor(() => expect(button).toBeEnabled())
      fireEvent.click(button)
      await waitFor(() => expect(refreshUser).toHaveBeenCalledOnce())
      expect(authorizationFailure).toHaveBeenCalledOnce()
      expect(button).toBeDisabled()
    } finally {
      window.removeEventListener('robopark:authorization-failure', authorizationFailure)
      view.unmount()
      await purgeOfflineScope()
    }
  })

  it('keeps the application usable when offline storage is unavailable', async () => {
    const auth = { user, loading: false, login: vi.fn(), logout: vi.fn(), refreshUser: vi.fn() }
    const park = { parkId: 1, selectedPark: null, parks: [], loading: false, locked: false, setParkId: vi.fn(), refreshParks: vi.fn() }
    const factory = vi.fn().mockRejectedValue(new Error('IndexedDB is unavailable'))

    render(<AuthContext.Provider value={auth}><ParkScopeContext.Provider value={park}><SyncProvider engineFactory={factory}><Probe /></SyncProvider></ParkScopeContext.Provider></AuthContext.Provider>)

    expect(await screen.findByText('attention')).toBeInTheDocument()
  })

  it('creates one engine for an authenticated scope and disposes it on park change', async () => {
    const first = { start: vi.fn(), dispose: vi.fn(), subscribe: vi.fn(() => () => undefined), getState: () => ({ status: 'idle' as const, pending: 0, conflicts: 0 }) } satisfies SyncEngineLike
    const second = { start: vi.fn(), dispose: vi.fn(), subscribe: vi.fn(() => () => undefined), getState: () => ({ status: 'idle' as const, pending: 0, conflicts: 0 }) } satisfies SyncEngineLike
    const factory = vi.fn().mockResolvedValueOnce(first).mockResolvedValueOnce(second)
    const auth = { user, loading: false, login: vi.fn(), logout: vi.fn(), refreshUser: vi.fn() }
    const park = (parkId: number) => ({ parkId, selectedPark: null, parks: [], loading: false, locked: false, setParkId: vi.fn(), refreshParks: vi.fn() })
    const view = render(<AuthContext.Provider value={auth}><ParkScopeContext.Provider value={park(1)}><SyncProvider engineFactory={factory}><Probe /></SyncProvider></ParkScopeContext.Provider></AuthContext.Provider>)
    await waitFor(() => expect(first.start).toHaveBeenCalledOnce())

    view.rerender(<AuthContext.Provider value={auth}><ParkScopeContext.Provider value={park(2)}><SyncProvider engineFactory={factory}><Probe /></SyncProvider></ParkScopeContext.Provider></AuthContext.Provider>)

    await waitFor(() => expect(second.start).toHaveBeenCalledOnce())
    expect(first.dispose).toHaveBeenCalledOnce()
    expect(screen.getByText('idle')).toBeInTheDocument()
    view.unmount()
    expect(second.dispose).toHaveBeenCalledOnce()
  })
})
