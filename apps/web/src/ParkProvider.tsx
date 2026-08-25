import { useCallback, useEffect, useMemo, useState, type PropsWithChildren } from 'react'
import { api, type Park } from './api'
import { useAuth } from './auth-context'
import {
  isAdminRole,
  ParkContext,
  readStoredParkId,
  resolveParkId,
  writeStoredParkId,
} from './park-context'

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

  const setParkId = useCallback(
    (id: number) => {
      if (parkLocked) return
      if (!parks.some((park) => park.id === id)) return
      setParkIdState(id)
      writeStoredParkId(id)
    },
    [parkLocked, parks],
  )

  const value = useMemo(
    () => ({ parkId, setParkId, parks, parksLoading, parkLocked }),
    [parkId, setParkId, parks, parksLoading, parkLocked],
  )

  return <ParkContext.Provider value={value}>{children}</ParkContext.Provider>
}
