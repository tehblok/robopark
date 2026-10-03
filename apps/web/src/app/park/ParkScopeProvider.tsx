import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type PropsWithChildren,
} from 'react'
import { useLocation, useSearchParams } from 'react-router-dom'
import { api, ApiError, type Park, type User } from '../../api'
import { useAuth } from '../../auth-context'
import {
  hasFleetParkScope,
  PARK_QUERY_KEY,
  PARK_STORAGE_KEY,
  ParkScopeContext,
  validParkId,
} from './parkScope'
import { activateDeviceResourceCache } from '../../lib/deviceResourceCache'
import { resourceStore } from '../../lib/resource'

function directoryKey(user: User, includeInactive: boolean): string {
  return `park-directory:${JSON.stringify([user.id, user.username, user.role, user.access_status,
    [...(user.permissions ?? [])].sort(), user.parks.map(park => park.id).sort((a, b) => a - b), includeInactive])}`
}

function activeParks(parks: Park[]): Park[] {
  return parks.filter((park) => park.is_active !== false)
}

const EMPTY_PARKS: Park[] = []

type ParkLoadState = {
  user: User | null
  fleetScope: boolean
  includeInactive: boolean
  parks: Park[]
  loading: boolean
  error: string | null
}

const PARK_LOAD_ERROR = 'Не удалось загрузить парки. Проверьте связь и повторите попытку.'

type ParkSelectionState = {
  context: string
  user: User | null
  fleetScope: boolean
  parkId: number | null
}

export function ParkScopeProvider({ children }: PropsWithChildren) {
  const { user, refreshUser } = useAuth()
  const { pathname } = useLocation()
  const [searchParams, setSearchParams] = useSearchParams()
  const allowAllParks = Boolean(user && ['admin', 'royal', 'operator'].includes(user.role)
    && ['/overview', '/analytics'].includes(pathname.replace(/\/$/, '')))
  const selectionContext = allowAllParks ? `insights:${searchParams.get(PARK_QUERY_KEY) ?? 'all'}` : 'single'
  const inventoryFleetScope = Boolean(user?.role === 'operator' && pathname.replace(/\/$/, '') === '/inventory')
  const includeInactiveInventoryParks = Boolean(user
    && ['admin', 'royal'].includes(user.role)
    && pathname.replace(/\/$/, '') === '/inventory'
    && searchParams.get('view') === 'export')
  const fleetScope = Boolean(user && (hasFleetParkScope(user) || inventoryFleetScope))
  const cacheKey = user && fleetScope ? directoryKey(user, includeInactiveInventoryParks) : null
  const cachedParks = cacheKey ? resourceStore.get<Park[]>(cacheKey) : undefined
  const [loadState, setLoadState] = useState<ParkLoadState | null>(null)
  const [selectionState, setSelectionState] = useState<ParkSelectionState | null>(null)
  const currentLoadState = loadState?.user === user && loadState.fleetScope === fleetScope && loadState.includeInactive === includeInactiveInventoryParks
    ? loadState
    : null
  const currentSelectionState =
    selectionState?.user === user && selectionState.fleetScope === fleetScope && selectionState.context === selectionContext
      ? selectionState
      : null
  const loadGeneration = useRef(0)
  const currentScope = useRef({ user, fleetScope, includeInactive: includeInactiveInventoryParks })
  const loadedParks = !user
    ? EMPTY_PARKS
    : fleetScope
      ? (currentLoadState?.parks ?? cachedParks ?? EMPTY_PARKS)
      : (currentLoadState?.parks ?? user.parks)
  const parks = useMemo(() => allowAllParks
    ? loadedParks.filter(park => park.is_active !== false && (user?.role !== 'operator' || user.parks.some(assigned => assigned.id === park.id)))
    : loadedParks, [allowAllParks, loadedParks, user])
  const loading = !user
    ? false
    : fleetScope
      ? (currentLoadState?.loading ?? cachedParks === undefined)
      : (currentLoadState?.loading ?? false)
  const parkId = currentSelectionState?.parkId ?? null

  const locked = user?.role === 'mechanic' && parks.length <= 1

  useEffect(() => {
    if (user) void activateDeviceResourceCache(user, parkId == null ? 'all' : String(parkId))
  }, [parkId, user])

  useLayoutEffect(() => {
    currentScope.current = { user, fleetScope, includeInactive: includeInactiveInventoryParks }
  }, [fleetScope, includeInactiveInventoryParks, user])

  const beginLoad = useCallback((
    requestUser: User | null,
    requestFleetScope: boolean,
    requestIncludeInactive: boolean,
    currentParks: Park[],
    background = false,
  ) => {
    const generation = ++loadGeneration.current
    setLoadState({
      user: requestUser,
      fleetScope: requestFleetScope,
      includeInactive: requestIncludeInactive,
      parks: currentParks,
      loading: !background,
      error: null,
    })
    return generation
  }, [])

  const commitLoad = useCallback((
    generation: number,
    requestUser: User | null,
    requestFleetScope: boolean,
    requestIncludeInactive: boolean,
    nextParks: Park[],
    error: string | null = null,
    remember = false,
  ) => {
    if (
      generation !== loadGeneration.current
      || requestUser !== currentScope.current.user
      || requestFleetScope !== currentScope.current.fleetScope
      || requestIncludeInactive !== currentScope.current.includeInactive
    ) return

    if (remember && requestUser && requestFleetScope) {
      resourceStore.set(directoryKey(requestUser, requestIncludeInactive), nextParks, false)
    }

    setLoadState({
      user: requestUser,
      fleetScope: requestFleetScope,
      includeInactive: requestIncludeInactive,
      parks: nextParks,
      loading: false,
      error,
    })
  }, [])

  useEffect(() => () => {
    loadGeneration.current += 1
  }, [])

  useEffect(() => {
    if (!user || !fleetScope) return

    const key = directoryKey(user, includeInactiveInventoryParks)
    const remembered = resourceStore.get<Park[]>(key)
    const fallbackParks = remembered ?? (includeInactiveInventoryParks ? user.parks : activeParks(user.parks))
    const generation = beginLoad(user, true, includeInactiveInventoryParks, fallbackParks, remembered !== undefined)
    let authoritative = false
    void resourceStore.hydrate<Park[]>(key).then(saved => {
      if (saved !== undefined && !authoritative) commitLoad(generation, user, true, includeInactiveInventoryParks, saved)
    })
    void api
      .parks()
      .then((nextParks) => {
        authoritative = true
        commitLoad(generation, user, true, includeInactiveInventoryParks, includeInactiveInventoryParks ? nextParks : activeParks(nextParks), null, true)
      })
      .catch((error) => {
        const denied = error instanceof ApiError && (error.status === 401 || error.status === 403)
        if (denied) { authoritative = true; resourceStore.evict(key) }
        commitLoad(
          generation,
          user,
          true,
          includeInactiveInventoryParks,
          denied ? [] : resourceStore.get<Park[]>(key) ?? fallbackParks,
          PARK_LOAD_ERROR,
        )
      })
  }, [beginLoad, commitLoad, fleetScope, includeInactiveInventoryParks, user])

  const writeSelection = useCallback(
    (nextParkId: number | null, replace: boolean) => {
      const nextSearchParams = new URLSearchParams(searchParams)
      if (nextParkId == null) {
        if (allowAllParks && parks.length) nextSearchParams.set(PARK_QUERY_KEY, 'all')
        else {
          nextSearchParams.delete(PARK_QUERY_KEY)
          sessionStorage.removeItem(PARK_STORAGE_KEY)
        }
      } else {
        nextSearchParams.set(PARK_QUERY_KEY, String(nextParkId))
        sessionStorage.setItem(PARK_STORAGE_KEY, String(nextParkId))
      }

      if (nextSearchParams.toString() !== searchParams.toString()) {
        setSearchParams(nextSearchParams, { replace })
      }
    },
    [allowAllParks, parks.length, searchParams, setSearchParams],
  )

  useEffect(() => {
    if (loading || !user) return

    const urlParkId = validParkId(parks, searchParams.get(PARK_QUERY_KEY))
    const storedParkId = validParkId(parks, sessionStorage.getItem(PARK_STORAGE_KEY))
    const explicitAll = allowAllParks && (!searchParams.has(PARK_QUERY_KEY) || searchParams.get(PARK_QUERY_KEY) === 'all')
    const nextParkId = explicitAll ? null : urlParkId ?? storedParkId ?? parks[0]?.id ?? null

    setSelectionState({ user, fleetScope, context: selectionContext, parkId: nextParkId })
    writeSelection(nextParkId, true)
  }, [allowAllParks, fleetScope, loading, parks, searchParams, selectionContext, user, writeSelection])

  const setParkId = useCallback(
    (id: number | null, options?: { replace?: boolean }) => {
      if (locked || (id === null ? !allowAllParks || !parks.length : !parks.some((park) => park.id === id))) return
      setSelectionState({ user, fleetScope, context: allowAllParks ? `insights:${id ?? 'all'}` : 'single', parkId: id })
      writeSelection(id, options?.replace ?? false)
    },
    [allowAllParks, fleetScope, locked, parks, user, writeSelection],
  )

  const refreshParks = useCallback(async () => {
    const requestUser = user
    const requestFleetScope = Boolean(requestUser && (hasFleetParkScope(requestUser) || inventoryFleetScope))
    const requestIncludeInactive = includeInactiveInventoryParks
    const generation = beginLoad(requestUser, requestFleetScope, requestIncludeInactive, parks)
    try {
      if (requestFleetScope) {
        const nextParks = await api.parks()
        commitLoad(
          generation,
          requestUser,
          requestFleetScope,
          requestIncludeInactive,
          requestIncludeInactive ? nextParks : activeParks(nextParks),
          null,
          true,
        )
        return
      }

      const refreshedUser = await refreshUser()
      commitLoad(
        generation,
        requestUser,
        requestFleetScope,
        requestIncludeInactive,
        refreshedUser.parks,
      )
    } catch (error) {
      if (requestUser && requestFleetScope && error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        resourceStore.evict(directoryKey(requestUser, requestIncludeInactive))
      }
      commitLoad(
        generation,
        requestUser,
        requestFleetScope,
        requestIncludeInactive,
        error instanceof ApiError && error.status === 403 ? [] : parks,
        PARK_LOAD_ERROR,
      )
      throw error
    }
  }, [beginLoad, commitLoad, includeInactiveInventoryParks, inventoryFleetScope, parks, refreshUser, user])

  const selectedPark = parks.find((park) => park.id === parkId) ?? null
  const value = useMemo(
    () => ({
      allowAllParks,
      parkId,
      selectedPark,
      parks,
      loading: loading || Boolean(allowAllParks && user && !currentLoadState?.error && (!currentSelectionState || (parkId !== null && !selectedPark))),
      loadError: currentLoadState?.error ?? null,
      locked,
      setParkId,
      refreshParks,
    }),
    [allowAllParks, parkId, selectedPark, parks, loading, user, currentLoadState?.error, currentSelectionState, locked, setParkId, refreshParks],
  )

  return <ParkScopeContext.Provider value={value}>{children}</ParkScopeContext.Provider>
}
