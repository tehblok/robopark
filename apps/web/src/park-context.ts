import { createContext, useContext } from 'react'
import type { Park } from './api'

export { PARK_STORAGE_KEY } from './app/park/parkScope'

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
  if (!context) throw new Error('useParkContext must be used within ParkProvider')
  return context
}
