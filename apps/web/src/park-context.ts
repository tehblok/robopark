import { createContext, useContext } from 'react'
import type { Park } from './api'

export const PARK_STORAGE_KEY = 'robopark-park-id'

export type ParkContextValue = {
  parkId: number | null
  setParkId: (id: number) => void
  parks: Park[]
  parksLoading: boolean
  parkLocked: boolean
}

export const ParkContext = createContext<ParkContextValue | null>(null)

export function useParkContext(): ParkContextValue {
  const context = useContext(ParkContext)
  if (!context) {
    throw new Error('useParkContext must be used within ParkProvider')
  }
  return context
}

export function readStoredParkId(): number | null {
  const raw = sessionStorage.getItem(PARK_STORAGE_KEY)
  if (!raw) return null
  const parsed = Number(raw)
  return Number.isFinite(parsed) ? parsed : null
}

export function writeStoredParkId(id: number | null): void {
  if (id == null) {
    sessionStorage.removeItem(PARK_STORAGE_KEY)
    return
  }
  sessionStorage.setItem(PARK_STORAGE_KEY, String(id))
}

export function isAdminRole(role: string): boolean {
  return role === 'admin' || role === 'royal'
}

export function resolveParkId(parks: Park[], locked: boolean): number | null {
  if (parks.length === 0) return null
  if (locked && parks.length === 1) return parks[0].id

  const stored = readStoredParkId()
  if (stored != null && parks.some((park) => park.id === stored)) {
    return stored
  }

  return parks[0].id
}
