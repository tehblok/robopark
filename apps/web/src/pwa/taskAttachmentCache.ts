import type { TrackerAttachment } from '../api'

const DATABASE = 'robopark-task-attachment-cache'
const STORE = 'attachments'
const MAX_BYTES = 32 * 1024 * 1024
const MAX_ENTRIES = 64
const MAX_AGE_MS = 5 * 60 * 1000

type CacheEntry = {
  key: string
  namespace: string
  blob: Blob
  bytes: number
  accessedAt: number
  storedAt: number
}

type AttachmentFetcher = (url: string) => Promise<Blob>
type AttachmentAuthorizer = (url: string) => Promise<void>

let activeNamespace: string | null = null
let generation = 0
let database: Promise<IDBDatabase> | null = null
let mutation = Promise.resolve()
let accessClock = 0
let hasActivatedNamespace = false
let pendingNamespace: string | null = null
let pendingActivation: Promise<void> | null = null

function nextAccessedAt(): number {
  accessClock = Math.max(Date.now(), accessClock + 1)
  return accessClock
}

function requestResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error ?? new Error('IndexedDB request failed'))
  })
}

function transactionDone(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve()
    transaction.onabort = transaction.onerror = () => reject(
      transaction.error ?? new Error('IndexedDB transaction failed'),
    )
  })
}

function openDatabase(): Promise<IDBDatabase> {
  if (!database) {
    database = new Promise((resolve, reject) => {
      const request = indexedDB.open(DATABASE, 1)
      request.onupgradeneeded = () => {
        const store = request.result.createObjectStore(STORE, { keyPath: 'key' })
        store.createIndex('namespace', 'namespace', { unique: false })
      }
      request.onsuccess = () => resolve(request.result)
      request.onerror = () => reject(request.error ?? new Error('IndexedDB open failed'))
    })
  }
  return database
}

function cacheKey(namespace: string, attachment: TrackerAttachment, url: string): string {
  return `${namespace}\0${attachment.id}\0${url}`
}

async function readEntry(key: string): Promise<CacheEntry | undefined> {
  const db = await openDatabase()
  const transaction = db.transaction(STORE, 'readonly')
  const store = transaction.objectStore(STORE)
  const entry = await requestResult(store.get(key)) as CacheEntry | undefined
  await transactionDone(transaction)
  return entry
}

async function touchBlob(key: string): Promise<void> {
  const db = await openDatabase()
  const transaction = db.transaction(STORE, 'readwrite')
  const store = transaction.objectStore(STORE)
  const entry = await requestResult(store.get(key)) as CacheEntry | undefined
  if (entry) {
    entry.accessedAt = nextAccessedAt()
    store.put(entry)
  }
  await transactionDone(transaction)
}

async function storeBlob(entry: CacheEntry): Promise<void> {
  if (entry.bytes > MAX_BYTES) return
  const db = await openDatabase()
  const transaction = db.transaction(STORE, 'readwrite')
  const store = transaction.objectStore(STORE)
  store.put(entry)
  const entries = await requestResult(store.getAll()) as CacheEntry[]
  let totalBytes = entries.reduce((sum, item) => sum + item.bytes, 0)
  const oldest = entries.sort((left, right) => left.accessedAt - right.accessedAt)
  while (oldest.length > MAX_ENTRIES || totalBytes > MAX_BYTES) {
    const evicted = oldest.shift()
    if (!evicted) break
    totalBytes -= evicted.bytes
    store.delete(evicted.key)
  }
  await transactionDone(transaction)
}

async function deleteBlob(key: string): Promise<void> {
  const db = await openDatabase()
  const transaction = db.transaction(STORE, 'readwrite')
  transaction.objectStore(STORE).delete(key)
  await transactionDone(transaction)
}

async function clearDatabase(): Promise<void> {
  if (typeof indexedDB === 'undefined') return
  const db = await openDatabase()
  const transaction = db.transaction(STORE, 'readwrite')
  transaction.objectStore(STORE).clear()
  await transactionDone(transaction)
  db.close()
  database = null
}

export async function activateTaskAttachmentCache(userId: number | string): Promise<void> {
  const nextNamespace = String(userId)
  if (activeNamespace === nextNamespace && pendingNamespace === null) return
  if (pendingNamespace === nextNamespace && pendingActivation) return pendingActivation

  const shouldPurge = hasActivatedNamespace
  hasActivatedNamespace = true
  const activationGeneration = ++generation
  activeNamespace = null
  pendingNamespace = nextNamespace
  const prepare = shouldPurge
    ? mutation.then(clearDatabase, clearDatabase)
    : mutation.then(() => undefined, () => undefined)
  mutation = prepare.catch(() => undefined)
  const activation = prepare.catch(() => undefined).then(() => {
    if (activationGeneration === generation) activeNamespace = nextNamespace
    if (pendingNamespace === nextNamespace) {
      pendingNamespace = null
      pendingActivation = null
    }
  })
  pendingActivation = activation
  return activation
}

export async function clearTaskAttachmentCache(): Promise<void> {
  const clearGeneration = ++generation
  activeNamespace = null
  pendingNamespace = null
  pendingActivation = null
  const clear = mutation.then(clearDatabase, clearDatabase)
  mutation = clear.catch(() => undefined)
  await clear.catch(() => undefined)
  if (generation === clearGeneration) hasActivatedNamespace = false
}

export async function loadTaskAttachment(
  attachment: TrackerAttachment,
  fetcher: AttachmentFetcher,
  authorize: AttachmentAuthorizer,
): Promise<string> {
  const url = attachment.url
  if (!url) throw new Error('task_attachment_url_missing')
  const namespace = activeNamespace
  const requestGeneration = generation
  if (namespace === null) throw new Error('task_attachment_session_changed')
  const key = cacheKey(namespace, attachment, url)

  if (key && typeof indexedDB !== 'undefined') {
    let cached: CacheEntry | undefined
    try {
      cached = await readEntry(key)
    } catch {
      // IndexedDB is an optional optimization; authenticated loading still works.
    }
    if (cached && (!cached.storedAt || Date.now() - cached.storedAt >= MAX_AGE_MS)) {
      await deleteBlob(key).catch(() => undefined)
      cached = undefined
    }
    if (cached && requestGeneration === generation && namespace === activeNamespace) {
      try {
        await authorize(url)
      } catch (error) {
        await deleteBlob(key).catch(() => undefined)
        throw error
      }
      if (requestGeneration !== generation || namespace !== activeNamespace) {
        throw new Error('task_attachment_session_changed')
      }
      await touchBlob(key).catch(() => undefined)
      return URL.createObjectURL(cached.blob)
    }
  }

  if (requestGeneration !== generation || namespace !== activeNamespace) {
    throw new Error('task_attachment_session_changed')
  }
  const blob = await fetcher(url)
  if (requestGeneration !== generation || namespace !== activeNamespace) {
    throw new Error('task_attachment_session_changed')
  }
  if (!blob.type.toLowerCase().startsWith('image/')) {
    throw new Error('task_attachment_not_image')
  }

  if (key && namespace !== null && typeof indexedDB !== 'undefined'
    && requestGeneration === generation && namespace === activeNamespace) {
    const write = mutation.then(async () => {
      if (requestGeneration !== generation || namespace !== activeNamespace) return
      await storeBlob({
        key,
        namespace,
        blob,
        bytes: blob.size,
        accessedAt: nextAccessedAt(),
        storedAt: Date.now(),
      })
      if (requestGeneration !== generation || namespace !== activeNamespace) {
        await deleteBlob(key)
      }
    })
    mutation = write.catch(() => undefined)
    await write.catch(() => undefined)
    if (requestGeneration !== generation || namespace !== activeNamespace) {
      await mutation.catch(() => undefined)
      throw new Error('task_attachment_session_changed')
    }
  }
  return URL.createObjectURL(blob)
}

export function releaseTaskAttachment(url: string): void {
  if (url.startsWith('blob:')) URL.revokeObjectURL(url)
}
