import { useSyncExternalStore } from 'react'

const KEY = 'robopark:share-target-ids'
const LEGACY_NOTICE_KEY = 'robopark:legacy-share-target-notice'
const CHANGE_EVENT = 'robopark:share-target-intents-changed'
const SHARE_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function storedIds(): string[] {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(KEY) ?? '[]')
    return Array.isArray(value) ? value.filter((id): id is string => typeof id === 'string' && SHARE_ID.test(id)).slice(0, 10) : []
  } catch { return [] }
}

function notifyChanged(): void { window.dispatchEvent(new Event(CHANGE_EVENT)) }

export function captureShareTargetId(): void {
  const url = new URL(window.location.href)
  const id = url.searchParams.get('shared')
  if (id === null) return
  if (!SHARE_ID.test(id)) {
    if (id === '1') {
      try { sessionStorage.setItem(LEGACY_NOTICE_KEY, '1') } catch { return /* Preserve the old marker if storage is blocked. */ }
    }
    url.searchParams.delete('shared')
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`)
    notifyChanged()
    return
  }
  try {
    const ids = storedIds()
    if (!ids.includes(id)) ids.push(id)
    sessionStorage.setItem(KEY, JSON.stringify(ids.slice(-10)))
  } catch { return /* Keep the URL capability if private mode blocks storage. */ }
  url.searchParams.delete('shared')
  window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`)
  notifyChanged()
}

export function pendingShareTargetId(): string | null {
  const direct = new URL(window.location.href).searchParams.get('shared')
  if (direct && SHARE_ID.test(direct)) return direct
  return storedIds()[0] ?? null
}

export function clearPendingShareTargetId(id: string): void {
  try { sessionStorage.setItem(KEY, JSON.stringify(storedIds().filter(item => item !== id))) } catch { /* URL fallback stays visible. */ }
  const url = new URL(window.location.href)
  if (url.searchParams.get('shared') === id) {
    url.searchParams.delete('shared')
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`)
  }
  notifyChanged()
}

function subscribe(callback: () => void): () => void {
  window.addEventListener(CHANGE_EVENT, callback)
  return () => window.removeEventListener(CHANGE_EVENT, callback)
}

export function usePendingShareTargetId(): string | null {
  return useSyncExternalStore(subscribe, pendingShareTargetId, () => null)
}

export function hasLegacyShareTargetNotice(): boolean {
  try { if (sessionStorage.getItem(LEGACY_NOTICE_KEY) === '1') return true } catch { /* URL fallback below. */ }
  return new URL(window.location.href).searchParams.get('shared') === '1'
}

export function clearLegacyShareTargetNotice(): void {
  try { sessionStorage.removeItem(LEGACY_NOTICE_KEY) } catch { /* No stored notice. */ }
  const url = new URL(window.location.href)
  if (url.searchParams.get('shared') === '1') {
    url.searchParams.delete('shared')
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`)
  }
  notifyChanged()
}

export function useLegacyShareTargetNotice(): boolean {
  return useSyncExternalStore(subscribe, hasLegacyShareTargetNotice, () => false)
}
