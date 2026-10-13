import type { Park, User } from '../../api'
import { desktopRuntime, sealDesktopJson, unsealDesktopJson } from '../../pwa/desktopRuntime'

export const OFFLINE_IDENTITY_KEY = 'robopark:offline-identity:v1'
// Matches the default server idle window. Offline use never extends it.
export const OFFLINE_IDENTITY_MAX_AGE_MS = 72 * 60 * 60 * 1000
const MAX_SNAPSHOT_CHARS = 64 * 1024
const DESKTOP_IDENTITY_DATABASE = 'robopark-native-identity'
const DESKTOP_IDENTITY_STORE = 'identity'
let desktopIdentity: User | null = null
let desktopIdentityGeneration = 0
let pendingDesktopIdentityWrite: Promise<void> | null = null

function requestResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => { request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error) })
}

async function desktopIdentityDb(): Promise<IDBDatabase> {
  const request = indexedDB.open(DESKTOP_IDENTITY_DATABASE, 1)
  request.onupgradeneeded = () => request.result.createObjectStore(DESKTOP_IDENTITY_STORE)
  return requestResult(request)
}

async function writeDesktopSnapshot(snapshot: unknown, generation: number): Promise<void> {
  const runtime = desktopRuntime()
  if (!runtime) return
  const protectedSnapshot = await sealDesktopJson(snapshot, { purpose: `offline-identity:${runtime.instanceId}` })
  const db = await desktopIdentityDb()
  try {
    if (generation !== desktopIdentityGeneration) return
    const transaction = db.transaction(DESKTOP_IDENTITY_STORE, 'readwrite')
    transaction.objectStore(DESKTOP_IDENTITY_STORE).put(protectedSnapshot, runtime.instanceId)
    await new Promise<void>((resolve, reject) => { transaction.oncomplete = () => resolve(); transaction.onabort = transaction.onerror = () => reject(transaction.error) })
  } finally { db.close() }
}

function validPark(park: Park): boolean {
  if (!park || !Number.isSafeInteger(park.id) || park.id <= 0
    || ![park.name, park.tag, park.timezone].every(value => typeof value === 'string' && value.length > 0 && value.length <= 160)
    || ![park.tracker_queue, park.tracker_priority, park.tracker_type].every(value => value == null || (typeof value === 'string' && value.length <= 160))
    || ![park.is_active, park.feature_reports, park.feature_blockers, park.feature_sla_repair, park.feature_backlog_alerts].every(value => value === undefined || typeof value === 'boolean')) return false
  try { new Intl.DateTimeFormat('en', { timeZone: park.timezone }); return true } catch { return false }
}

function compactPark(park: Park): Park {
  const { id, name, tag, timezone, is_active, tracker_queue, tracker_priority, tracker_type,
    feature_reports, feature_blockers, feature_sla_repair, feature_backlog_alerts } = park
  return { id, name, tag, timezone, is_active, tracker_queue, tracker_priority, tracker_type,
    feature_reports, feature_blockers, feature_sla_repair, feature_backlog_alerts }
}

/** Local UI identity only: no password, cookie, token or new server authority. */
export function writeOfflineIdentity(user: User): void {
  try {
    if (user.access_status !== 'approved' || user.must_change_password) {
      localStorage.removeItem(OFFLINE_IDENTITY_KEY)
      desktopIdentity = null
      void clearDesktopOfflineIdentity()
      return
    }
    const { id, username, role, access_status, permissions, tracker_login, must_change_password, screenshot_guard } = user
    const snapshot = JSON.stringify({ version: 1, verifiedAt: Date.now(), user: {
      id, username, role, access_status, permissions, tracker_login, must_change_password, screenshot_guard,
      parks: user.parks.map(compactPark),
    } })
    if (snapshot.length <= MAX_SNAPSHOT_CHARS) {
      if (desktopRuntime()) {
        localStorage.removeItem(OFFLINE_IDENTITY_KEY)
        desktopIdentity = validateSnapshot(snapshot)
        const generation = ++desktopIdentityGeneration
        const write = writeDesktopSnapshot(JSON.parse(snapshot), generation)
          .catch(() => { if (generation === desktopIdentityGeneration) desktopIdentity = null })
        pendingDesktopIdentityWrite = write
        void write.finally(() => { if (pendingDesktopIdentityWrite === write) pendingDesktopIdentityWrite = null })
      } else localStorage.setItem(OFFLINE_IDENTITY_KEY, snapshot)
    }
    else {
      localStorage.removeItem(OFFLINE_IDENTITY_KEY)
      desktopIdentity = null
      void clearDesktopOfflineIdentity()
    }
  } catch { /* Storage disabled: the active in-memory session still works. */ }
}

export function readOfflineIdentity(): User | null {
  if (desktopRuntime()) return desktopIdentity
  try {
    const raw = localStorage.getItem(OFFLINE_IDENTITY_KEY)
    return raw ? validateSnapshot(raw) : null
  } catch { return null }
}

function validateSnapshot(raw: string): User | null {
  try {
    if (raw.length > MAX_SNAPSHOT_CHARS) return null
    const snapshot = JSON.parse(raw)
    const age = Date.now() - snapshot.verifiedAt
    const user = snapshot.user
    const text = (value: unknown) => typeof value === 'string' && value.length > 0 && value.length <= 160
    if (snapshot.version !== 1 || !Number.isFinite(age) || age < 0 || age >= OFFLINE_IDENTITY_MAX_AGE_MS
      || !user || !Number.isSafeInteger(user.id) || user.id <= 0 || !text(user.username) || !text(user.role)
      || user.access_status !== 'approved' || user.must_change_password === true
      || (user.permissions !== undefined && (!Array.isArray(user.permissions) || user.permissions.length > 128 || !user.permissions.every(text)))
      || !Array.isArray(user.parks) || user.parks.length > 256
      || !user.parks.every(validPark)) return null
    return {
      id: user.id, username: user.username, role: user.role, access_status: 'approved',
      permissions: user.permissions, tracker_login: typeof user.tracker_login === 'string' ? user.tracker_login : null,
      must_change_password: false, screenshot_guard: user.screenshot_guard === true,
      parks: user.parks.map(compactPark),
    }
  } catch { return null }
}

export async function hydrateDesktopOfflineIdentity(): Promise<void> {
  const runtime = desktopRuntime()
  if (!runtime || typeof indexedDB === 'undefined') return
  const generation = ++desktopIdentityGeneration
  desktopIdentity = null
  try {
    const db = await desktopIdentityDb()
    const transaction = db.transaction(DESKTOP_IDENTITY_STORE, 'readonly')
    const protectedSnapshot = await requestResult(transaction.objectStore(DESKTOP_IDENTITY_STORE).get(runtime.instanceId))
    db.close()
    if (!protectedSnapshot) return
    const snapshot = await unsealDesktopJson(protectedSnapshot, { purpose: `offline-identity:${runtime.instanceId}` })
    const raw = JSON.stringify(snapshot)
    if (raw.length > MAX_SNAPSHOT_CHARS) return
    if (generation === desktopIdentityGeneration) desktopIdentity = validateSnapshot(raw)
  } catch { if (generation === desktopIdentityGeneration) desktopIdentity = null }
}

export async function clearDesktopOfflineIdentity(): Promise<void> {
  const generation = ++desktopIdentityGeneration
  desktopIdentity = null
  if (!desktopRuntime() || typeof indexedDB === 'undefined') return
  try {
    const db = await desktopIdentityDb()
    if (generation !== desktopIdentityGeneration) { db.close(); return }
    const transaction = db.transaction(DESKTOP_IDENTITY_STORE, 'readwrite')
    transaction.objectStore(DESKTOP_IDENTITY_STORE).clear()
    await new Promise<void>((resolve, reject) => { transaction.oncomplete = () => resolve(); transaction.onabort = transaction.onerror = () => reject(transaction.error) })
    db.close()
  } catch { /* Logout remains fail-closed in memory. */ }
}

export async function flushDesktopOfflineIdentityForTests(): Promise<void> {
  if (import.meta.env.MODE !== 'test') throw new Error('desktop_identity_test_flush_forbidden')
  await pendingDesktopIdentityWrite
}
