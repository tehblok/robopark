import type { ReportAttachmentKind } from '../../api'
import type { OfflineScope } from '../../pwa/offlineTypes'

export type ReportPhotoDraft = {
  key: string
  ownerKey: string
  revision: string
  activeForm: 'question' | 'problem'
  trackerKey: string
  title: string
  body: string
  createdReportId: number | null
  attachmentKind: ReportAttachmentKind
  attachment: { blob: Blob; name: string; lastModified: number } | null
}

const DATABASE = 'robopark-report-drafts-v1'
const STORE = 'drafts'
const META = 'generations'
// Each mounted tab binds to the generation it first observed for this scope.
const leases = new Map<string, string>()
let sessionGeneration: string | undefined
let queue: Promise<unknown> = Promise.resolve()
let epoch = 0
const scopeEpoch = new Map<string, number>()
const retiredKey = (key: string, ownerKey: string) => `${key}:retired:${encodeURIComponent(ownerKey)}`

function enqueue<T>(action: () => Promise<T>): Promise<T> {
  const result = queue.then(action)
  queue = result.catch(() => {})
  return result
}

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') { reject(new Error('Хранилище файлов недоступно.')); return }
    const request = indexedDB.open(DATABASE, 2)
    let blocked = false
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE)) request.result.createObjectStore(STORE, { keyPath: 'key' })
      if (!request.result.objectStoreNames.contains(META)) request.result.createObjectStore(META)
    }
    request.onsuccess = () => { if (blocked) request.result.close(); else resolve(request.result) }
    request.onerror = () => reject(request.error)
    request.onblocked = () => { blocked = true; reject(new Error('Закройте другую вкладку, чтобы сохранить черновик.')) }
  })
}

async function transaction<T>(mode: IDBTransactionMode, run: (store: IDBObjectStore, result: (value: T) => void, fail: (error: Error) => void, meta: IDBObjectStore) => void): Promise<T> {
  const db = await openDatabase()
  return new Promise<T>((resolve, reject) => {
    const tx = db.transaction([STORE, META], mode)
    let value: T
    let failure: Error | null = null
    tx.oncomplete = () => { db.close(); resolve(value) }
    tx.onabort = tx.onerror = () => { db.close(); reject(failure ?? tx.error ?? new Error('Не удалось сохранить черновик.')) }
    try {
      run(tx.objectStore(STORE), (next) => { value = next }, (error) => { failure = error; tx.abort() }, tx.objectStore(META))
    } catch (error) { failure = error as Error; tx.abort() }
  })
}

function acceptsSession(lease: string): boolean {
  const generation = (JSON.parse(lease) as [string, string])[0]
  if (sessionGeneration === undefined) sessionGeneration = generation
  return sessionGeneration === generation
}

function readLease(meta: IDBObjectStore, key: string, done: (lease: string) => void) {
  const global = meta.get('global')
  global.onsuccess = () => {
    const scope = meta.get(`scope:${key}`)
    scope.onsuccess = () => done(JSON.stringify([global.result ?? '', scope.result ?? '']))
  }
}

export function readReportPhotoDraft(key: string): Promise<ReportPhotoDraft | null> {
  const requestedEpoch = epoch
  const requestedScope = scopeEpoch.get(key)
  return enqueue(async () => {
    const result = await transaction<{ draft: ReportPhotoDraft | null; lease: string }>('readonly', (store, done, _fail, meta) => {
      readLease(meta, key, (lease) => {
        const request = store.get(key)
        request.onsuccess = () => done({ draft: request.result ?? null, lease })
      })
    })
    if (requestedEpoch !== epoch || requestedScope !== scopeEpoch.get(key)) return null
    if (!acceptsSession(result.lease)) return null
    if (!leases.has(key)) leases.set(key, result.lease)
    return leases.get(key) === result.lease ? result.draft : null
  })
}

export function writeReportPhotoDraft(draft: ReportPhotoDraft): Promise<void> {
  const requestedEpoch = epoch
  const requestedScope = scopeEpoch.get(draft.key)
  return enqueue(async () => {
    if (requestedEpoch !== epoch || requestedScope !== scopeEpoch.get(draft.key)) return
    const textBytes = 2 * (draft.title.length + draft.body.length + draft.trackerKey.length + draft.ownerKey.length)
    if (textBytes > 256 * 1024) throw new Error('Текст черновика превышает лимит 256 КиБ.')
    const bytes = draft.attachment?.blob.size ?? 0
    const maxBytes = draft.attachmentKind === 'client_log' ? 64 * 1024 : 15 * 1024 * 1024
    if (bytes > maxBytes) throw new Error(`Файл превышает лимит ${draft.attachmentKind === 'client_log' ? '64 КиБ' : '15 МиБ'}.`)
    await transaction<void>('readwrite', (store, done, fail, meta) => {
      readLease(meta, draft.key, (lease) => {
        // Revocation can happen after opening the transaction but before this reply.
        // Never let an old reply bind the next session to the previous generation.
        if (requestedEpoch !== epoch || requestedScope !== scopeEpoch.get(draft.key)) { done(); return }
        const expected = leases.get(draft.key)
        if (!acceptsSession(lease) || (expected !== undefined && expected !== lease)) {
          fail(new Error('Сеанс черновика завершён в другой вкладке. Войдите снова перед продолжением.'))
          return
        }
        leases.set(draft.key, lease)
        const request = store.getAll()
        request.onsuccess = () => {
          if (requestedEpoch !== epoch || requestedScope !== scopeEpoch.get(draft.key)) { done(); return }
          const stored = request.result as ReportPhotoDraft[]
          const occupied = stored.find(item => item.key === draft.key)
          const archiveKey = occupied && occupied.ownerKey !== draft.ownerKey
            ? retiredKey(draft.key, occupied.ownerKey) : null
          // Account for the records that this transaction will actually retain.
          // A principal change preserves the occupied slot under its retired key.
          const others = stored.filter(item => item.key !== draft.key && item.key !== archiveKey)
          if (archiveKey && occupied) others.push(occupied)
          const total = others.reduce((sum, item) => sum + (item.attachment?.blob.size ?? 0), bytes)
          if (others.length >= 8 || total > 60 * 1024 * 1024) {
            fail(new Error('Хранилище черновиков заполнено. Удалите ненужные черновики.'))
            return
          }
          if (occupied && archiveKey) {
            // A new principal may inherit the physical account/park key after
            // a browser restart. Preserve the previous owner before replacing it.
            store.put({ ...occupied, key: archiveKey })
            const nextScopeLease = crypto.randomUUID()
            meta.put(nextScopeLease, `scope:${draft.key}`)
            const [globalLease] = JSON.parse(lease) as [string, string]
            leases.set(draft.key, JSON.stringify([globalLease, nextScopeLease]))
            scopeEpoch.set(draft.key, (scopeEpoch.get(draft.key) ?? 0) + 1)
          }
          store.put(draft)
          done()
        }
      })
    })
  })
}

/** Hides a draft atomically while retaining its attachment for exact reauthorization. */
export function quarantineReportPhotoDraft(key: string, ownerKey: string): Promise<void> {
  scopeEpoch.set(key, (scopeEpoch.get(key) ?? 0) + 1)
  leases.delete(key)
  return enqueue(() => transaction<void>('readwrite', (store, done, _fail, meta) => {
    const request = store.get(key)
    request.onsuccess = () => {
      const draft = request.result as ReportPhotoDraft | undefined
      if (draft?.ownerKey === ownerKey) {
        store.put({ ...draft, key: retiredKey(key, ownerKey) })
        store.delete(key)
        meta.put(crypto.randomUUID(), `scope:${key}`)
      }
      done()
    }
  }))
}

export function restoreReportPhotoDraft(key: string, ownerKey: string): Promise<void> {
  return enqueue(() => transaction<void>('readwrite', (store, done, _fail) => {
    const active = store.get(key)
    active.onsuccess = () => {
      if (active.result) { done(); return }
      const archivedKey = retiredKey(key, ownerKey)
      const archived = store.get(archivedKey)
      archived.onsuccess = () => {
        const draft = archived.result as ReportPhotoDraft | undefined
        if (draft?.ownerKey === ownerKey) {
          store.put({ ...draft, key })
          store.delete(archivedKey)
        }
        done()
      }
    }
  }))
}

function belongsToScope(draft: ReportPhotoDraft, scope: OfflineScope): boolean {
  try {
    const owner = JSON.parse(draft.ownerKey) as unknown[]
    const parks = owner[7] as unknown[]
    const selected = owner[8] as unknown[] | null
    return String(owner[0]) === scope.account && owner[1] === scope.principal && owner[3] === scope.role
      && Array.isArray(owner[6]) && (owner[6] as string[]).join(',') === scope.permissions
      && Array.isArray(parks) && parks.map(item => String((item as unknown[])[0])).join(',') === scope.parkAccess
      && (selected === null
        ? (scope.role === 'admin' || scope.role === 'royal') && scope.park === 'all'
        : String(selected?.[0]) === scope.park)
  } catch { return false }
}

export function quarantineReportPhotoDraftsForScope(scope: OfflineScope): Promise<void> {
  return enqueue(async () => {
    const drafts = await transaction<ReportPhotoDraft[]>('readonly', (store, done) => {
      const request = store.getAll()
      request.onsuccess = () => done(request.result as ReportPhotoDraft[])
    })
    for (const draft of drafts) {
      if (!draft.key.includes(':retired:') && belongsToScope(draft, scope)) {
        // Inline transaction: this queued operation must not await another enqueue.
        scopeEpoch.set(draft.key, (scopeEpoch.get(draft.key) ?? 0) + 1)
        leases.delete(draft.key)
        await transaction<void>('readwrite', (store, done, _fail, meta) => {
          store.put({ ...draft, key: retiredKey(draft.key, draft.ownerKey) })
          store.delete(draft.key)
          meta.put(crypto.randomUUID(), `scope:${draft.key}`)
          done()
        })
      }
    }
  })
}

export function restoreReportPhotoDraftsForScope(scope: OfflineScope): Promise<void> {
  return enqueue(async () => {
    const drafts = await transaction<ReportPhotoDraft[]>('readonly', (store, done) => {
      const request = store.getAll()
      request.onsuccess = () => done(request.result as ReportPhotoDraft[])
    })
    for (const draft of drafts) {
      if (!draft.key.includes(':retired:') || !belongsToScope(draft, scope)) continue
      const key = draft.key.slice(0, draft.key.indexOf(':retired:'))
      await transaction<void>('readwrite', (store, done) => {
        const active = store.get(key)
        active.onsuccess = () => {
          if (!active.result) {
            store.put({ ...draft, key })
            store.delete(draft.key)
          }
          done()
        }
      })
    }
  })
}

/** No revision means owner revocation: pending writes are invalidated immediately. */
export function deleteReportPhotoDraft(key: string, revision?: string): Promise<void> {
  if (revision === undefined) {
    scopeEpoch.set(key, (scopeEpoch.get(key) ?? 0) + 1)
    leases.delete(key)
  }
  return enqueue(() => transaction<void>('readwrite', (store, done, _fail, meta) => {
    if (revision === undefined) {
      meta.put(crypto.randomUUID(), `scope:${key}`)
      store.delete(key)
      done()
      return
    }
    const request = store.get(key)
    request.onsuccess = () => {
      if ((request.result as ReportPhotoDraft | undefined)?.revision === revision) store.delete(key)
      done()
    }
  }))
}

/** Invalidates in-flight reads and queued saves synchronously, before clearing disk. */
export function clearReportPhotoDrafts(): Promise<void> {
  epoch += 1
  scopeEpoch.clear()
  leases.clear()
  sessionGeneration = undefined
  if (typeof indexedDB === 'undefined') return Promise.resolve()
  return enqueue(() => transaction<void>('readwrite', (store, done, _fail, meta) => {
    meta.clear()
    meta.put(crypto.randomUUID(), 'global')
    store.clear()
    done()
  }))
}
