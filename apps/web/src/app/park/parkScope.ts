import { createContext, useContext } from 'react'
import type { Park, User } from '../../api'

export const PARK_QUERY_KEY = 'park'
export const PARK_STORAGE_KEY = 'robopark-park-id'

export type ParkScopeUser = Pick<User, 'role' | 'permissions'>

export function hasFleetParkScope(user: ParkScopeUser): boolean {
  return user.role === 'admin'
    || user.role === 'royal'
    || (user.permissions ?? []).includes('parks.manage')
}

export type ParkScopeValue = {
  parkId: number | null
  selectedPark: Park | null
  parks: Park[]
  loading: boolean
  locked: boolean
  setParkId: (id: number, options?: { replace?: boolean }) => void
  refreshParks: () => Promise<void>
}

export const ParkScopeContext = createContext<ParkScopeValue | null>(null)

export function useParkScope(): ParkScopeValue {
  const value = useContext(ParkScopeContext)
  if (!value) throw new Error('useParkScope must be used inside ParkScopeProvider')
  return value
}

export function validParkId(parks: readonly Park[], raw: string | null): number | null {
  if (!raw || !/^\d+$/.test(raw)) return null
  const id = Number(raw)
  return parks.some((item) => item.id === id) ? id : null
}
