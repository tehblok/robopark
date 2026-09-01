import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { User } from '../api'
import { AuthContext, type AuthContextValue } from '../auth-context'
import { ParkContext, type ParkContextValue } from '../park-context'
import { Analytics } from './Analytics'

const parkContext: ParkContextValue = {
  parkId: null,
  setParkId: vi.fn(),
  parks: [],
  parksLoading: false,
  parkLocked: false,
}

function renderForRole(role: string) {
  const user: User = {
    id: 1,
    username: 'person',
    role,
    access_status: 'approved',
    permissions: role === 'admin' ? ['nav.dashboard'] : [],
    parks: [],
  }
  const auth: AuthContextValue = {
    user,
    loading: false,
    login: vi.fn(),
    refreshUser: vi.fn(),
    logout: vi.fn(),
  }
  return (
    <MemoryRouter>
      <AuthContext.Provider value={auth}>
        <ParkContext.Provider value={parkContext}>
          <Analytics />
        </ParkContext.Provider>
      </AuthContext.Provider>
    </MemoryRouter>
  )
}

describe('Analytics', () => {
  it('can switch from operator to admin without changing hook order', () => {
    const view = render(renderForRole('operator'))
    expect(screen.getByText('Сейчас по Tracker')).toBeInTheDocument()

    view.rerender(renderForRole('admin'))

    expect(
      screen.getByText('Расширенная аналитика скоро появится'),
    ).toBeInTheDocument()
  })
})
