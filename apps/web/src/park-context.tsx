import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type PropsWithChildren,
} from 'react'
import { api, type Park } from './api'
import { useAuth } from './auth-context'

export const PARK_STORAGE_KEY = 'robopark-park-id'

type ParkContextValue = {
  parkId: number | null
  setParkId: (id: number) => void
  parks: Park[]
  parksLoading: boolean
  parkLocked: boolean
}

const ParkContext = createContext<ParkContextValue | null>(null)

function readStoredParkId(): number | null {
  const raw = sessionStorage.getItem(PARK_STORAGE_KEY)
  if (!raw) return null
  const parsed = Number(raw)
  return Number.isFinite(parsed) ? parsed : null
}

function writeStoredParkId(id: number | null): void {
  if (id == null) {
    sessionStorage.removeItem(PARK_STORAGE_KEY)
    return
  }
  sessionStorage.setItem(PARK_STORAGE_KEY, String(id))
}

function isAdminRole(role: string): boolean {
  return role === 'admin' || role === 'royal'
}

function resolveParkId(parks: Park[], locked: boolean): number | null {
  if (parks.length === 0) return null
  if (locked && parks.length === 1) return parks[0].id

  const stored = readStoredParkId()
  if (stored != null && parks.some((park) => park.id === stored)) {
    return stored
  }

  return parks[0].id
}

export function ParkProvider({ children }: PropsWithChildren) {
  const { user } = useAuth()
  const [parks, setParks] = useState<Park[]>([])
  const [parksLoading, setParksLoading] = useState(false)
  const [parkId, setParkIdState] = useState<number | null>(() => readStoredParkId())

  const parkLocked = user?.role === 'mechanic'

  useEffect(() => {
    if (!user) {
      setParks([])
      setParkIdState(null)
      writeStoredParkId(null)
      return
    }

    if (isAdminRole(user.role)) {
      setParksLoading(true)
      api
        .parks()
        .then((list) => setParks(list.filter((park) => park.is_active !== false)))
        .catch(() => setParks([]))
        .finally(() => setParksLoading(false))
      return
    }

    setParks(user.parks ?? [])
  }, [user])

  useEffect(() => {
    const next = resolveParkId(parks, parkLocked)
    setParkIdState((current) => {
      if (current === next) return current
      writeStoredParkId(next)
      return next
    })
  }, [parks, parkLocked])

  const setParkId = (id: number) => {
    if (parkLocked) return
    if (!parks.some((park) => park.id === id)) return
    setParkIdState(id)
    writeStoredParkId(id)
  }

  const value = useMemo(
    () => ({ parkId, setParkId, parks, parksLoading, parkLocked }),
    [parkId, parks, parksLoading, parkLocked],
  )

  return <ParkContext.Provider value={value}>{children}</ParkContext.Provider>
}

export function useParkContext(): ParkContextValue {
  const context = useContext(ParkContext)
  if (!context) {
    throw new Error('useParkContext must be used within ParkProvider')
  }
  return context
}
