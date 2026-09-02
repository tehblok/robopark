import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type PropsWithChildren,
} from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, type Park } from '../../api'
import { useAuth } from '../../auth-context'
import {
  hasFleetParkScope,
  PARK_QUERY_KEY,
  PARK_STORAGE_KEY,
  ParkScopeContext,
  validParkId,
} from './parkScope'

function activeParks(parks: Park[]): Park[] {
  return parks.filter((park) => park.is_active !== false)
}

export function ParkScopeProvider({ children }: PropsWithChildren) {
  const { user, refreshUser } = useAuth()
  const [searchParams, setSearchParams] = useSearchParams()
  const fleetScope = Boolean(user && hasFleetParkScope(user))
  const [parks, setParks] = useState<Park[]>(() =>
    user && !hasFleetParkScope(user) ? user.parks : [],
  )
  const [loading, setLoading] = useState(fleetScope)
  const [parkId, setParkIdState] = useState<number | null>(null)

  const locked = user?.role === 'mechanic' && parks.length <= 1

  useEffect(() => {
    let cancelled = false

    if (!user) {
      setParks([])
      setLoading(false)
      return () => {
        cancelled = true
      }
    }

    if (!hasFleetParkScope(user)) {
      setParks(user.parks)
      setLoading(false)
      return () => {
        cancelled = true
      }
    }

    setLoading(true)
    void api
      .parks()
      .then((nextParks) => {
        if (!cancelled) setParks(activeParks(nextParks))
      })
      .catch(() => {
        if (!cancelled) setParks([])
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [user])

  const writeSelection = useCallback(
    (nextParkId: number | null, replace: boolean) => {
      const nextSearchParams = new URLSearchParams(searchParams)
      if (nextParkId == null) {
        nextSearchParams.delete(PARK_QUERY_KEY)
        sessionStorage.removeItem(PARK_STORAGE_KEY)
      } else {
        nextSearchParams.set(PARK_QUERY_KEY, String(nextParkId))
        sessionStorage.setItem(PARK_STORAGE_KEY, String(nextParkId))
      }

      if (nextSearchParams.toString() !== searchParams.toString()) {
        setSearchParams(nextSearchParams, { replace })
      }
    },
    [searchParams, setSearchParams],
  )

  useEffect(() => {
    if (loading) return

    const urlParkId = validParkId(parks, searchParams.get(PARK_QUERY_KEY))
    const storedParkId = validParkId(parks, sessionStorage.getItem(PARK_STORAGE_KEY))
    const nextParkId = urlParkId ?? storedParkId ?? parks[0]?.id ?? null

    setParkIdState(nextParkId)
    writeSelection(nextParkId, true)
  }, [loading, parks, searchParams, writeSelection])

  const setParkId = useCallback(
    (id: number, options?: { replace?: boolean }) => {
      if (locked || !parks.some((park) => park.id === id)) return
      setParkIdState(id)
      writeSelection(id, options?.replace ?? false)
    },
    [locked, parks, writeSelection],
  )

  const refreshParks = useCallback(async () => {
    setLoading(true)
    try {
      if (user && hasFleetParkScope(user)) {
        setParks(activeParks(await api.parks()))
        return
      }

      const refreshedUser = await refreshUser()
      setParks(refreshedUser.parks)
    } finally {
      setLoading(false)
    }
  }, [refreshUser, user])

  const selectedPark = parks.find((park) => park.id === parkId) ?? null
  const value = useMemo(
    () => ({
      parkId,
      selectedPark,
      parks,
      loading,
      locked,
      setParkId,
      refreshParks,
    }),
    [parkId, selectedPark, parks, loading, locked, setParkId, refreshParks],
  )

  return <ParkScopeContext.Provider value={value}>{children}</ParkScopeContext.Provider>
}
