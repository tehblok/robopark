import type { ReportAttachmentKind } from '../../api'

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
          const others = (request.result as ReportPhotoDraft[]).filter((item) => item.key !== draft.key)
          const total = others.reduce((sum, item) => sum + (item.attachment?.blob.size ?? 0), bytes)
          if (others.length >= 8 || total > 60 * 1024 * 1024) {
            fail(new Error('Хранилище черновиков заполнено. Удалите ненужные черновики.'))
            return
          }
          store.put(draft)
          done()
        }
      })
    })
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
