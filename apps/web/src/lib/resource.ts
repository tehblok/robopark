/**
 * Stale-while-revalidate hook + shared store for РобоПарк screens.
 *
 * The idea:
 *   1. When a screen mounts, show whatever is already cached — a paint from
 *      memory or a mirror in localStorage — with no spinner.
 *   2. In the background, ask the API for the current version. Show a thin
 *      top progress bar only while any resource is revalidating.
 *   3. When the fresh payload arrives, swap it in atomically.
 *
 * The bulk of the win comes from *not* looking at `<SkeletonList>` when the
 * server round-trip is 10+ seconds (Tracker). It also cuts request storms:
 * revisiting a page reuses the last response until the loader completes.
 *
 * Persistence is opt-out per key via `persist: false` — the cache mirror lives
 * under a single localStorage prefix so we can wipe it on login/logout to
 * avoid leaking data between accounts on the same browser.
 */

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'

const LS_PREFIX = 'robopark:res:'
const LS_VERSION = 1
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

  get<T>(key: string): T | undefined {
    const hit = this.mem.get(key)
    if (hit) {
      if (isFresh(hit)) return hit.data as T
      this.mem.delete(key)
      removeFromStorage(key)
    }
    const parsed = readFromStorage(key)
    if (parsed && isFresh(parsed)) {
      this.mem.set(key, parsed)
      return parsed.data as T
    }
    if (parsed) removeFromStorage(key)
    return undefined
  }

  set(key: string, data: unknown, persist: boolean): void {
    const entry: StoredEntry = { v: LS_VERSION, updatedAt: Date.now(), data }
    this.mem.set(key, entry)
    if (persist) writeToStorage(key, entry)
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
      removeFromStorageByPrefix(keyOrPrefix)
    } else {
      if (this.mem.delete(keyOrPrefix)) notified.add(keyOrPrefix)
      removeFromStorage(keyOrPrefix)
    }
    for (const k of notified) this.notify(k)
  }

  clearAll(): void {
    invalidateAllPendingLoads()
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
    if (parsed.v !== LS_VERSION) return null
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
  /** Mirror successful responses to localStorage (default: true). */
  persist?: boolean
  /** Fire the loader on mount even if we already have cached data (default: true). */
  refreshOnMount?: boolean
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
  const persist = opts.persist ?? true
  const refreshOnMount = opts.refreshOnMount ?? true
  const enabled = opts.enabled ?? true
  const trackProgress = opts.trackProgress ?? true

  const initial = enabled ? resourceStore.get<T>(key) : undefined
  const [data, setData] = useState<T | undefined>(initial)
  const [error, setError] = useState<unknown>(null)
  const [isRevalidating, setIsRevalidating] = useState(false)

  const loaderRef = useRef(loader)
  loaderRef.current = loader
  const requestIdRef = useRef(0)

  useEffect(() => {
    if (!enabled) return
    setData(resourceStore.get<T>(key))
    const unsub = resourceStore.subscribe(key, () => {
      setData(resourceStore.get<T>(key))
    })
    return unsub
  }, [key, enabled])

  const runLoad = useCallback(async () => {
    if (!enabled) return
    const requestId = ++requestIdRef.current
    const loadGeneration = captureLoadGeneration(key)
    setIsRevalidating(true)
    if (trackProgress) inFlight.begin()
    try {
      const fresh = await coalesceLoader(key, () => loaderRef.current())
      if (
        requestId !== requestIdRef.current ||
        !isLoadGenerationCurrent(key, loadGeneration)
      ) return
      resourceStore.set(key, fresh, persist)
      setError(null)
    } catch (loadError) {
      if (
        requestId !== requestIdRef.current ||
        !isLoadGenerationCurrent(key, loadGeneration)
      ) return
      setError(loadError)
    } finally {
      if (requestId === requestIdRef.current) {
        setIsRevalidating(false)
      }
      if (trackProgress) inFlight.end()
    }
  }, [enabled, key, persist, trackProgress])

  useEffect(() => {
    if (!enabled) return
    const cached = resourceStore.get<T>(key)
    if (refreshOnMount || cached === undefined) {
      void runLoad()
    }
  }, [enabled, key, refreshOnMount, runLoad])

  return {
    data,
    error,
    isRevalidating,
    isLoading: data === undefined && isRevalidating,
    refresh: runLoad,
  }
}
