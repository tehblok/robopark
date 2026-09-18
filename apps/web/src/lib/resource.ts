/**
 * Stale-while-revalidate hook + shared store for park-management screens.
 *
 * The idea:
 *   1. When a screen mounts, show whatever is already cached — a paint from
 *      memory or a mirror in localStorage — with no spinner.
 *   2. Revalidate stale responses automatically while visible and online.
 *      Only initial or explicitly requested loads use the top progress bar.
 *   3. When the fresh payload arrives, swap it in atomically.
 *
 * The bulk of the win comes from *not* looking at `<SkeletonList>` when the
 * server round-trip is 10+ seconds (Tracker). It also cuts request storms:
 * revisiting a page reuses the last response until the loader completes.
 *
 * Persistence is opt-in per key via `persist: true`. Protected responses stay
 * in bounded memory and are dropped on login/logout or access changes.
 */

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { periodicDelay, resumeDelay, retryAfterMs } from './pollingSchedule'

const LS_PREFIX = 'robopark:res:'
const LS_VERSION = 2
export const RESOURCE_REFRESH_MS = 30_000
const MEMORY_MAX_ENTRIES = 128
/** Drop persisted snapshots older than this; next visit is a cold load. */
export const LS_MAX_AGE_MS = 12 * 60 * 60 * 1000

type StoredEntry = {
  v: number
  updatedAt: number
  data: unknown
}

type LoadGeneration = {
  all: symbol
  key: symbol
}

const inflightLoaders = new Map<string, Promise<unknown>>()
const loadGenerations = new Map<string, symbol>()
let allLoadsGeneration = Symbol('all-resource-loads')

function currentKeyGeneration(key: string): symbol {
  const current = loadGenerations.get(key)
  if (current) return current
  const initial = Symbol(key)
  loadGenerations.set(key, initial)
  return initial
}

function captureLoadGeneration(key: string): LoadGeneration {
  return {
    all: allLoadsGeneration,
    key: currentKeyGeneration(key),
  }
}

function isLoadGenerationCurrent(key: string, generation: LoadGeneration): boolean {
  return (
    generation.all === allLoadsGeneration &&
    generation.key === currentKeyGeneration(key)
  )
}

function invalidatePendingLoads(
  keyOrPrefix: string,
  prefix: boolean,
): void {
  if (prefix) {
    for (const key of loadGenerations.keys()) {
      if (key.startsWith(keyOrPrefix)) {
        loadGenerations.set(key, Symbol(key))
      }
    }
    for (const key of inflightLoaders.keys()) {
      if (key.startsWith(keyOrPrefix)) inflightLoaders.delete(key)
    }
    return
  }

  loadGenerations.set(keyOrPrefix, Symbol(keyOrPrefix))
  inflightLoaders.delete(keyOrPrefix)
}

function invalidateAllPendingLoads(): void {
  allLoadsGeneration = Symbol('all-resource-loads')
  loadGenerations.clear()
  inflightLoaders.clear()
}

class ResourceStore {
  private mem = new Map<string, StoredEntry>()
  private subs = new Map<string, Set<() => void>>()
  private refreshSubs = new Map<string, Set<() => void>>()
  private activeScopes = new Map<string, string>()

  private remember(key: string, entry: StoredEntry): void {
    this.mem.delete(key)
    this.mem.set(key, entry)
    while (this.mem.size > MEMORY_MAX_ENTRIES) {
      const oldest = this.mem.keys().next().value
      if (oldest === undefined) break
      this.mem.delete(oldest)
    }
  }

  get<T>(key: string, allowStorage = false): T | undefined {
    const hit = this.mem.get(key)
    if (hit) {
      if (isFresh(hit)) {
        this.remember(key, hit)
        return hit.data as T
      }
      this.mem.delete(key)
      removeFromStorage(key)
    }
    if (!allowStorage) {
      removeFromStorage(key)
      return undefined
    }
    const parsed = readFromStorage(key)
    if (parsed && isFresh(parsed)) {
      this.remember(key, parsed)
      return parsed.data as T
    }
    if (parsed) removeFromStorage(key)
    return undefined
  }

  isStale(key: string, staleTimeMs: number, allowStorage = false): boolean {
    if (this.get(key, allowStorage) === undefined) return true
    return Date.now() - (this.mem.get(key)?.updatedAt ?? 0) >= staleTimeMs
  }

  updatedAt(key: string, allowStorage = false): number | null {
    if (this.get(key, allowStorage) === undefined) return null
    return this.mem.get(key)?.updatedAt ?? null
  }

  set(key: string, data: unknown, persist: boolean): void {
    const entry: StoredEntry = { v: LS_VERSION, updatedAt: Date.now(), data }
    this.remember(key, entry)
    if (persist) writeToStorage(key, entry)
    else removeFromStorage(key)
    this.notify(key)
  }

  /** Drop denied data without retiring sibling consumers of the same request. */
  evict(key: string): void {
    this.mem.delete(key)
    removeFromStorage(key)
    this.notify(key)
  }

  invalidate(keyOrPrefix: string, { prefix = false }: { prefix?: boolean } = {}): void {
    invalidatePendingLoads(keyOrPrefix, prefix)
    const notified = new Set<string>()
    if (prefix) {
      for (const k of Array.from(this.mem.keys())) {
        if (k.startsWith(keyOrPrefix)) {
          this.mem.delete(k)
          notified.add(k)
        }
      }
      for (const k of this.subs.keys()) {
        if (k.startsWith(keyOrPrefix)) notified.add(k)
      }
      removeFromStorageByPrefix(keyOrPrefix)
    } else {
      this.mem.delete(keyOrPrefix)
      notified.add(keyOrPrefix)
      removeFromStorage(keyOrPrefix)
    }
    for (const k of notified) this.notify(k)
  }

  /** Retire unfinished requests on route exit but keep settled in-memory data. */
  cancelPending(keyOrPrefix: string, { prefix = false }: { prefix?: boolean } = {}): void {
    invalidatePendingLoads(keyOrPrefix, prefix)
  }

  /** Switching an authorization scope invalidates its previously cached data. */
  activateScope(family: string, prefix: string): void {
    const previous = this.activeScopes.get(family)
    if (previous && previous !== prefix) this.invalidate(previous, { prefix: true })
    this.activeScopes.set(family, prefix)
  }

  /** Ask mounted consumers to reload in the background, retaining cached data. */
  revalidate(keyOrPrefix: string, { prefix = false }: { prefix?: boolean } = {}): void {
    for (const [key, listeners] of this.refreshSubs) {
      if (prefix ? key.startsWith(keyOrPrefix) : key === keyOrPrefix) {
        listeners.forEach(listener => listener())
      }
    }
  }

  clearAll(): void {
    invalidateAllPendingLoads()
    this.activeScopes.clear()
    const keys = Array.from(this.subs.keys())
    this.mem.clear()
    removeFromStorageByPrefix('')
    for (const key of keys) this.notify(key)
  }

  subscribe(key: string, fn: () => void): () => void {
    let listeners = this.subs.get(key)
    if (!listeners) {
      listeners = new Set()
      this.subs.set(key, listeners)
    }
    listeners.add(fn)
    return () => {
      const remaining = this.subs.get(key)
      if (!remaining) return
      remaining.delete(fn)
      if (remaining.size === 0) this.subs.delete(key)
    }
  }

  subscribeRevalidate(key: string, fn: () => void): () => void {
    let listeners = this.refreshSubs.get(key)
    if (!listeners) {
      listeners = new Set()
      this.refreshSubs.set(key, listeners)
    }
    listeners.add(fn)
    return () => {
      listeners.delete(fn)
      if (listeners.size === 0) this.refreshSubs.delete(key)
    }
  }

  private notify(key: string): void {
    this.subs.get(key)?.forEach((fn) => fn())
  }
}

function isFresh(entry: StoredEntry): boolean {
  return Date.now() - entry.updatedAt < LS_MAX_AGE_MS
}

function readFromStorage(key: string): StoredEntry | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.localStorage.getItem(LS_PREFIX + key)
    if (!raw) return null
    const parsed = JSON.parse(raw) as StoredEntry
    if (parsed.v !== LS_VERSION) {
      window.localStorage.removeItem(LS_PREFIX + key)
      return null
    }
    return parsed
  } catch {
    return null
  }
}

function writeToStorage(key: string, entry: StoredEntry): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(LS_PREFIX + key, JSON.stringify(entry))
  } catch {
    // Quota exceeded / private mode / disabled storage — memory tier still works.
  }
}

function removeFromStorage(key: string): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.removeItem(LS_PREFIX + key)
  } catch {
    // ignore
  }
}

function removeFromStorageByPrefix(prefix: string): void {
  if (typeof window === 'undefined') return
  try {
    const full = LS_PREFIX + prefix
    for (let i = window.localStorage.length - 1; i >= 0; i--) {
      const k = window.localStorage.key(i)
      if (k && k.startsWith(full)) window.localStorage.removeItem(k)
    }
  } catch {
    // ignore
  }
}

/** One-time upgrade cleanup; no current screen opts into disk persistence. */
export function pruneLegacyResourceSnapshots(): void {
  if (typeof window === 'undefined') return
  try {
    for (let index = window.localStorage.length - 1; index >= 0; index--) {
      const key = window.localStorage.key(index)
      if (key?.startsWith(LS_PREFIX)) window.localStorage.removeItem(key)
    }
  } catch {
    // Storage may be disabled; in-memory reads still work.
  }
}

class InFlightCounter {
  private count = 0
  private subs = new Set<() => void>()

  begin(): void {
    this.count += 1
    this.notify()
  }
  end(): void {
    this.count = Math.max(0, this.count - 1)
    this.notify()
  }

  get isActive(): boolean {
    return this.count > 0
  }

  subscribe(fn: () => void): () => void {
    this.subs.add(fn)
    return () => {
      this.subs.delete(fn)
    }
  }

  private notify(): void {
    this.subs.forEach((fn) => fn())
  }
}

export const resourceStore = new ResourceStore()
export const inFlight = new InFlightCounter()

/** Overlapping loaders for the same key share one in-flight Promise. */
export function coalesceLoader<T>(key: string, loader: () => Promise<T>): Promise<T> {
  const existing = inflightLoaders.get(key)
  if (existing) return existing as Promise<T>
  const pending = loader().finally(() => {
    if (inflightLoaders.get(key) === pending) inflightLoaders.delete(key)
  })
  inflightLoaders.set(key, pending)
  return pending as Promise<T>
}

export function resetCoalescingForTests(): void {
  inflightLoaders.clear()
}

export function useIsRevalidating(): boolean {
  return useSyncExternalStore(
    (fn) => inFlight.subscribe(fn),
    () => inFlight.isActive,
    () => false,
  )
}

type Options = {
  /** Mirror only explicitly safe responses to localStorage (default: false). */
  persist?: boolean
  /** Explicit true forces mount refresh; by default only stale data revalidates. */
  refreshOnMount?: boolean
  /** Fresh cache avoids repeat loads on mount, focus and timer ticks. */
  staleTimeMs?: number
  /** Visible/online refresh cadence. Zero disables automatic polling and resume. */
  refreshIntervalMs?: number
  /** Set to false to defer loading until a real key is available. */
  enabled?: boolean
  /**
   * Count this load toward the global progress bar (default: true).
   * Set false for high-frequency polls so the bar does not flicker.
   */
  trackProgress?: boolean
}

export type CachedResource<T> = {
  data: T | undefined
  /** Time of the last successful response, retained during failed refreshes. */
  updatedAt: number | null
  error: unknown
  /** True only when we have no cached data and a load is in flight. */
  isLoading: boolean
  /** True whenever a background refetch is currently running. */
  isRevalidating: boolean
  /** Trigger a manual refetch (e.g. after mutations). */
  refresh: () => Promise<void>
}

export function useCachedResource<T>(
  key: string,
  loader: () => Promise<T>,
  opts: Options = {},
): CachedResource<T> {
  const persist = opts.persist ?? false
  const refreshOnMount = opts.refreshOnMount
  const staleTimeMs = opts.staleTimeMs ?? RESOURCE_REFRESH_MS
  const refreshIntervalMs = opts.refreshIntervalMs ?? RESOURCE_REFRESH_MS
  const enabled = opts.enabled ?? true
  const trackProgress = opts.trackProgress ?? true

  const initial = enabled ? resourceStore.get<T>(key, persist) : undefined
  const [data, setData] = useState<T | undefined>(initial)
  const [syncTime, setSyncTime] = useState(() => ({ key, time: enabled ? resourceStore.updatedAt(key, persist) : null }))
  const [error, setError] = useState<unknown>(null)
  const [isRevalidating, setIsRevalidating] = useState(false)

  const loaderRef = useRef(loader)
  loaderRef.current = loader
  const requestIdRef = useRef(0)
  const ownerGenerationRef = useRef(Symbol('cached-resource-owner'))
  const retryRef = useRef({ failures: 0, after: 0, blocked: false })

  useEffect(() => {
    const ownerGeneration = ownerGenerationRef.current
    retryRef.current = { failures: 0, after: 0, blocked: false }
    setError(null)
    return () => {
      if (ownerGenerationRef.current === ownerGeneration) {
        ownerGenerationRef.current = Symbol('cached-resource-owner')
      }
    }
  }, [key])

  useEffect(() => {
    if (!enabled) return
    setData(resourceStore.get<T>(key, persist))
    setSyncTime({ key, time: resourceStore.updatedAt(key, persist) })
    const unsub = resourceStore.subscribe(key, () => {
      setData(resourceStore.get<T>(key, persist))
      setSyncTime({ key, time: resourceStore.updatedAt(key, persist) })
    })
    return unsub
  }, [key, enabled, persist])

  const runLoad = useCallback(async (background = false) => {
    if (!enabled) return
    const requestId = ++requestIdRef.current
    const loadGeneration = captureLoadGeneration(key)
    const ownerGeneration = ownerGenerationRef.current
    setIsRevalidating(true)
    const showProgress = trackProgress && !background
    if (showProgress) inFlight.begin()
    try {
      const fresh = await coalesceLoader(key, () => loaderRef.current())
      if (
        requestId !== requestIdRef.current ||
        ownerGeneration !== ownerGenerationRef.current ||
        !isLoadGenerationCurrent(key, loadGeneration)
      ) return
      resourceStore.set(key, fresh, persist)
      retryRef.current = { failures: 0, after: 0, blocked: false }
      setError(null)
    } catch (loadError) {
      if (
        requestId !== requestIdRef.current ||
        ownerGeneration !== ownerGenerationRef.current ||
        !isLoadGenerationCurrent(key, loadGeneration)
      ) return
      const status = loadError && typeof loadError === 'object' && 'status' in loadError ? loadError.status : undefined
      const failures = retryRef.current.failures + 1
      retryRef.current = {
        failures,
        after: Date.now() + Math.max(retryAfterMs(loadError), Math.min(300_000, Math.max(refreshIntervalMs, 1_000) * 2 ** Math.min(failures - 1, 8))),
        blocked: status === 401 || status === 403,
      }
      if (retryRef.current.blocked) resourceStore.evict(key)
      setError(loadError)
    } finally {
      if (
        requestId === requestIdRef.current &&
        ownerGeneration === ownerGenerationRef.current
      ) {
        setIsRevalidating(false)
      }
      if (showProgress) inFlight.end()
    }
  }, [enabled, key, persist, trackProgress, refreshIntervalMs])

  const canLoadAutomatically = useCallback(() => (
    !document.hidden && document.visibilityState !== 'hidden' && navigator.onLine !== false &&
    !retryRef.current.blocked && Date.now() >= retryRef.current.after
  ), [])

  useEffect(() => {
    if (!enabled) return
    return resourceStore.subscribeRevalidate(key, () => {
      if (canLoadAutomatically()) void runLoad(true)
    })
  }, [enabled, key, canLoadAutomatically, runLoad])

  useEffect(() => {
    if (!enabled || !canLoadAutomatically()) return
    const cached = resourceStore.get<T>(key, persist)
    if (cached === undefined || refreshOnMount === true ||
        (refreshOnMount !== false && resourceStore.isStale(key, staleTimeMs, persist))) {
      void runLoad(cached !== undefined)
    }
  }, [enabled, key, refreshOnMount, staleTimeMs, runLoad, canLoadAutomatically, persist])

  useEffect(() => {
    if (!enabled) return
    const refreshIfStale = () => {
      // Draft-backed resources opt out of periodic replacement, but a cold
      // mount deferred while offline still needs its first response on return.
      if (refreshIntervalMs <= 0 && resourceStore.get(key, persist) !== undefined) return
      if (!canLoadAutomatically() || inflightLoaders.has(key) || !resourceStore.isStale(key, staleTimeMs, persist)) return
      void runLoad(true)
    }
    let timer: number | undefined
    let resumeTimer: number | undefined
    const schedule = (initial = false) => {
      if (refreshIntervalMs <= 0) return
      timer = window.setTimeout(() => { refreshIfStale(); schedule() }, periodicDelay(refreshIntervalMs, initial))
    }
    const resume = () => {
      if (resumeTimer !== undefined || !canLoadAutomatically()) return
      const delay = resumeDelay()
      if (delay === 0) { refreshIfStale(); return }
      resumeTimer = window.setTimeout(() => { resumeTimer = undefined; refreshIfStale() }, delay)
    }
    schedule(true)
    document.addEventListener('visibilitychange', resume)
    window.addEventListener('focus', resume)
    window.addEventListener('online', resume)
    return () => {
      window.clearTimeout(timer)
      window.clearTimeout(resumeTimer)
      document.removeEventListener('visibilitychange', resume)
      window.removeEventListener('focus', resume)
      window.removeEventListener('online', resume)
    }
  }, [enabled, key, refreshIntervalMs, staleTimeMs, runLoad, canLoadAutomatically, persist])

  const refresh = useCallback(() => runLoad(), [runLoad])

  return {
    data,
    updatedAt: enabled && syncTime.key === key ? syncTime.time : null,
    error,
    isRevalidating,
    isLoading: data === undefined && isRevalidating,
    refresh,
  }
}
