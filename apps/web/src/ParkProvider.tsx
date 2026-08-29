import { useCallback, useEffect, useMemo, useState, type PropsWithChildren } from 'react'
import { api, type Park, type User } from './api'
import { useAuth } from './auth-context'
import {
  isAdminRole,
  ParkContext,
  resolveParkId,
  writeStoredParkId,
} from './park-context'

function assignedParks(user: User | null): Park[] {
  return user && !isAdminRole(user.role) ? (user.parks ?? []) : []
}

function parkLockedFor(user: User | null): boolean {
  return user?.role === 'mechanic' && (user.parks?.length ?? 0) <= 1
}

export function ParkProvider({ children }: PropsWithChildren) {
  const { user } = useAuth()
  const [parks, setParks] = useState<Park[]>(() => assignedParks(user))
  const [parksLoading, setParksLoading] = useState(() => Boolean(user && isAdminRole(user.role)))
  const [parkId, setParkIdState] = useState<number | null>(() =>
    resolveParkId(assignedParks(user), parkLockedFor(user)),
  )

  const parkLocked = parkLockedFor(user)

  useEffect(() => {
    if (!user) {
      setParks([])
      setParksLoading(false)
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
    setParksLoading(false)
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
