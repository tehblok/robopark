import { render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Park, User } from './api'
import { AuthContext, type AuthContextValue } from './auth-context'
import { ParkProvider } from './ParkProvider'
import { PARK_STORAGE_KEY, useParkContext } from './park-context'

const park: Park = { id: 7, name: 'Север', tag: 'north' }

function operatorUser(parks: Park[]): User {
  return {
    id: 1,
    username: 'op',
    role: 'operator',
    access_status: 'approved',
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

describe('ParkProvider', () => {
  afterEach(() => {
    sessionStorage.removeItem(PARK_STORAGE_KEY)
  })

  it('uses assigned parks on the first render for operators', () => {
    const parks = [park]
    const first: { parks: Park[]; parksLoading: boolean; parkId: number | null }[] = []

    function Observer() {
      const value = useParkContext()
      first.push({ parks: value.parks, parksLoading: value.parksLoading, parkId: value.parkId })
      return null
    }

    render(
      <AuthContext.Provider value={authValue(operatorUser(parks))}>
        <ParkProvider>
          <Observer />
        </ParkProvider>
      </AuthContext.Provider>,
    )

    expect(first[0]?.parks).toEqual(parks)
    expect(first[0]?.parksLoading).toBe(false)
    expect(first[0]?.parkId).toBe(park.id)
  })
})
