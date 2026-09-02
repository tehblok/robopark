import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Park, User } from './api'
import { AuthContext, type AuthContextValue } from './auth-context'
import { useParkScope } from './app/park/parkScope'
import { ParkProvider } from './ParkProvider'
import { PARK_STORAGE_KEY, useParkContext } from './park-context'

const park = (id: number): Park => ({
  id,
  name: `Парк ${id}`,
  tag: `park-${id}`,
  is_active: true,
})

function operatorUser(parks: Park[]): User {
  return {
    id: 1,
    username: 'op',
    role: 'operator',
    access_status: 'approved',
    permissions: ['nav.dashboard'],
    parks,
  }
}

function authValue(user: User | null): AuthContextValue {
  return {
    user,
    loading: false,
    login: vi.fn(),
    refreshUser: vi.fn(),
    logout: vi.fn(),
  }
}

describe('ParkProvider compatibility bridge', () => {
  afterEach(() => {
    sessionStorage.clear()
  })

  it('derives legacy aliases and selection from the same URL-backed scope', async () => {
    const actor = userEvent.setup()

    function Observer() {
      const scope = useParkScope()
      const legacy = useParkContext()
      const location = useLocation()
      return (
        <>
          <output data-testid="core-park">{scope.parkId ?? 'none'}</output>
          <output data-testid="legacy-park">{legacy.parkId ?? 'none'}</output>
          <output data-testid="legacy-loading">{String(legacy.parksLoading)}</output>
          <output data-testid="legacy-locked">{String(legacy.parkLocked)}</output>
          <output data-testid="location">{location.search}</output>
          <button type="button" onClick={() => legacy.setParkId(9)}>
            Выбрать парк 9
          </button>
        </>
      )
    }

    render(
      <MemoryRouter initialEntries={['/overview?park=7']}>
        <AuthContext.Provider value={authValue(operatorUser([park(7), park(9)]))}>
          <ParkProvider>
            <Observer />
          </ParkProvider>
        </AuthContext.Provider>
      </MemoryRouter>,
    )

    expect(await screen.findByTestId('core-park')).toHaveTextContent('7')
    expect(screen.getByTestId('legacy-park')).toHaveTextContent('7')
    expect(screen.getByTestId('legacy-loading')).toHaveTextContent('false')
    expect(screen.getByTestId('legacy-locked')).toHaveTextContent('false')

    await actor.click(screen.getByRole('button', { name: 'Выбрать парк 9' }))

    expect(screen.getByTestId('core-park')).toHaveTextContent('9')
    expect(screen.getByTestId('legacy-park')).toHaveTextContent('9')
    expect(screen.getByTestId('location')).toHaveTextContent('?park=9')
    expect(sessionStorage.getItem(PARK_STORAGE_KEY)).toBe('9')
  })
})
