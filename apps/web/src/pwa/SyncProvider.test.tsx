import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { User } from '../api'
import { AuthContext } from '../auth-context'
import { ParkScopeContext } from '../app/park/parkScope'
import { SyncProvider, useSync, type SyncEngineLike } from './SyncProvider'

const user: User = { id: 1, username: 'mech', role: 'mechanic', access_status: 'approved', permissions: ['tracker.read'], parks: [] }

function Probe() {
  const { state } = useSync()
  return <span>{state.status}</span>
}

describe('SyncProvider', () => {
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
