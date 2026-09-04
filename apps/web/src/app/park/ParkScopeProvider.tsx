import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type PropsWithChildren,
} from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, type Park, type User } from '../../api'
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

const EMPTY_PARKS: Park[] = []

type ParkLoadState = {
  user: User | null
  fleetScope: boolean
  parks: Park[]
  loading: boolean
}

type ParkSelectionState = {
  user: User | null
  fleetScope: boolean
  parkId: number | null
}

export function ParkScopeProvider({ children }: PropsWithChildren) {
  const { user, refreshUser } = useAuth()
  const [searchParams, setSearchParams] = useSearchParams()
  const fleetScope = Boolean(user && hasFleetParkScope(user))
  const [loadState, setLoadState] = useState<ParkLoadState | null>(null)
  const [selectionState, setSelectionState] = useState<ParkSelectionState | null>(null)
  const currentLoadState = loadState?.user === user && loadState.fleetScope === fleetScope
    ? loadState
    : null
  const currentSelectionState =
    selectionState?.user === user && selectionState.fleetScope === fleetScope
      ? selectionState
      : null
  const loadGeneration = useRef(0)
  const currentScope = useRef({ user, fleetScope })
  const parks = !user
    ? EMPTY_PARKS
    : fleetScope
      ? (currentLoadState?.parks ?? EMPTY_PARKS)
      : (currentLoadState?.parks ?? user.parks)
  const loading = !user
    ? false
    : fleetScope
      ? (currentLoadState?.loading ?? true)
      : (currentLoadState?.loading ?? false)
  const parkId = currentSelectionState?.parkId ?? null

  const locked = user?.role === 'mechanic' && parks.length <= 1

  useLayoutEffect(() => {
    currentScope.current = { user, fleetScope }
  }, [fleetScope, user])

  const beginLoad = useCallback((
    requestUser: User | null,
    requestFleetScope: boolean,
    currentParks: Park[],
  ) => {
    const generation = ++loadGeneration.current
    setLoadState({
      user: requestUser,
      fleetScope: requestFleetScope,
      parks: currentParks,
      loading: true,
    })
    return generation
  }, [])

  const commitLoad = useCallback((
    generation: number,
    requestUser: User | null,
    requestFleetScope: boolean,
    nextParks: Park[],
  ) => {
    if (
      generation !== loadGeneration.current
      || requestUser !== currentScope.current.user
      || requestFleetScope !== currentScope.current.fleetScope
    ) return

    setLoadState({
      user: requestUser,
      fleetScope: requestFleetScope,
      parks: nextParks,
      loading: false,
    })
  }, [])

  useEffect(() => () => {
    loadGeneration.current += 1
  }, [])

  useEffect(() => {
    if (!user || !hasFleetParkScope(user)) return

    const generation = beginLoad(user, true, [])
    void api
      .parks()
      .then((nextParks) => {
        commitLoad(generation, user, true, activeParks(nextParks))
      })
      .catch(() => {
        commitLoad(generation, user, true, [])
      })
  }, [beginLoad, commitLoad, user])

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

    setSelectionState({ user, fleetScope, parkId: nextParkId })
    writeSelection(nextParkId, true)
  }, [fleetScope, loading, parks, searchParams, user, writeSelection])

  const setParkId = useCallback(
    (id: number, options?: { replace?: boolean }) => {
      if (locked || !parks.some((park) => park.id === id)) return
      setSelectionState({ user, fleetScope, parkId: id })
      writeSelection(id, options?.replace ?? false)
    },
    [fleetScope, locked, parks, user, writeSelection],
  )

  const refreshParks = useCallback(async () => {
    const requestUser = user
    const requestFleetScope = Boolean(requestUser && hasFleetParkScope(requestUser))
    const generation = beginLoad(requestUser, requestFleetScope, parks)
    try {
      if (requestFleetScope) {
        commitLoad(
          generation,
          requestUser,
          requestFleetScope,
          activeParks(await api.parks()),
        )
        return
      }

      const refreshedUser = await refreshUser()
      commitLoad(
        generation,
        requestUser,
        requestFleetScope,
        refreshedUser.parks,
      )
    } catch (error) {
      commitLoad(generation, requestUser, requestFleetScope, parks)
      throw error
    }
  }, [beginLoad, commitLoad, parks, refreshUser, user])

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
