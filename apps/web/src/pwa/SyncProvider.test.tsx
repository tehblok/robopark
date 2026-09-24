import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type User } from '../api'
import { AuthContext } from '../auth-context'
import { ParkScopeContext } from '../app/park/parkScope'
import { SyncProvider, useSync, type SyncEngineLike } from './SyncProvider'

const user: User = { id: 1, username: 'mech', role: 'mechanic', access_status: 'approved', permissions: ['tracker.read'], parks: [] }

function Probe() {
  const { state } = useSync()
  return <span>{state.status}</span>
}

describe('SyncProvider', () => {
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

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
