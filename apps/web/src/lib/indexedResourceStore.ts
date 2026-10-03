import { cachePolicy, type DeviceCachePolicy } from './cachePolicy'

const DATABASE = 'robopark-resource-cache'
const STORE = 'resources'
const DB_VERSION = 1

export type AccountScope = {
  account: string
  principal?: string
  accessStatus?: string
  role: string
  permissions: string
  park: string
  parkAccess?: string
  schema: number
}

type Entry = {
  id: string
  scope: string
  key: string
  data: unknown
  bytes: number
  updatedAt: number
  accessedAt: number
}

export type PersistedResource<T> = { data: T, updatedAt: number }

function requestResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error ?? new Error('IndexedDB request failed'))
  })
}

function transactionDone(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve()
    transaction.onabort = transaction.onerror = () => reject(transaction.error ?? new Error('IndexedDB transaction failed'))
  })
}

function scopeKey(scope: AccountScope): string {
  return [scope.account, scope.principal ?? '', scope.accessStatus ?? '', scope.role, scope.permissions, scope.park, scope.parkAccess ?? '', String(scope.schema)]
    .map(value => encodeURIComponent(value)).join('|')
}

function entryBytes(value: unknown): number {
  if (value instanceof Blob) return value.size
  try { return new TextEncoder().encode(JSON.stringify(value)).byteLength } catch { return 0 }
}

function isQuotaError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'QuotaExceededError'
}

export class IndexedResourceStore {
  private generation = 0
  private readonly db: IDBDatabase
  private readonly scope: string
  private readonly policy: DeviceCachePolicy

  private constructor(
    db: IDBDatabase,
    scope: string,
    policy: DeviceCachePolicy,
  ) {
    this.db = db
    this.scope = scope
    this.policy = policy
  }

  static async open(scope: AccountScope, overrides: Partial<DeviceCachePolicy> = {}): Promise<IndexedResourceStore> {
    if (typeof indexedDB === 'undefined') throw new Error('IndexedDB is unavailable')
    const request = indexedDB.open(DATABASE, DB_VERSION)
    request.onupgradeneeded = () => {
      const store = request.result.createObjectStore(STORE, { keyPath: 'id' })
      store.createIndex('scope', 'scope', { unique: false })
    }
    const db = await requestResult(request)
    const result = new IndexedResourceStore(db, scopeKey(scope), cachePolicy(overrides))
    await result.purgeOtherScopes()
    await result.prune()
    return result
  }

  static async clearAll(): Promise<void> {
    if (typeof indexedDB === 'undefined') return
    const request = indexedDB.open(DATABASE, DB_VERSION)
    request.onupgradeneeded = () => {
      const store = request.result.createObjectStore(STORE, { keyPath: 'id' })
      store.createIndex('scope', 'scope', { unique: false })
    }
    const db = await requestResult(request)
    try {
      if (!db.objectStoreNames.contains(STORE)) return
      const transaction = db.transaction(STORE, 'readwrite')
      transaction.objectStore(STORE).clear()
      await transactionDone(transaction)
    } finally { db.close() }
  }

  captureGeneration(): number { return this.generation }

  isGenerationCurrent(generation: number): boolean { return generation === this.generation }

  async get<T>(key: string): Promise<T | undefined> {
    return (await this.getEntry<T>(key))?.data
  }

  async getEntry<T>(key: string): Promise<PersistedResource<T> | undefined> {
    const transaction = this.db.transaction(STORE, 'readwrite')
    const store = transaction.objectStore(STORE)
    const entry = await requestResult(store.get(`${this.scope}\0${key}`)) as Entry | undefined
    if (!entry || Date.now() - entry.updatedAt >= this.policy.staleMs) {
      if (entry) store.delete(entry.id)
      await transactionDone(transaction)
      return undefined
    }
    entry.accessedAt = Date.now()
    store.put(entry)
    await transactionDone(transaction)
    return { data: entry.data as T, updatedAt: entry.updatedAt }
  }

  async set(
    key: string,
    data: unknown,
    generation = this.generation,
    updatedAt = Date.now(),
  ): Promise<boolean> {
    if (generation !== this.generation) return false
    if (/^(?:photo|image):original(?::|$)/i.test(key)) throw new Error('Original photos are not persisted')
    const now = Date.now()
    const entry: Entry = {
      id: `${this.scope}\0${key}`, scope: this.scope, key, data,
      bytes: entryBytes(data), updatedAt, accessedAt: now,
    }
    if (entry.bytes > this.policy.maxBytes) return false
    try {
      await this.put(entry)
    } catch (error) {
      if (!isQuotaError(error)) throw error
      await this.evictOldest(Math.max(1, Math.ceil((await this.globalStats()).entries / 2)))
      await this.put(entry)
    }
    await this.prune()
    if (generation !== this.generation) {
      await this.delete(key)
      return false
    }
    return true
  }

  async purge(): Promise<void> {
    this.generation += 1
    const entries = await this.entries()
    const transaction = this.db.transaction(STORE, 'readwrite')
    const store = transaction.objectStore(STORE)
    for (const entry of entries) store.delete(entry.id)
    await transactionDone(transaction)
  }

  async delete(key: string): Promise<void> {
    const transaction = this.db.transaction(STORE, 'readwrite')
    transaction.objectStore(STORE).delete(`${this.scope}\0${key}`)
    await transactionDone(transaction)
  }

  async deletePrefix(prefix: string): Promise<void> {
    const matches = (await this.entries()).filter(entry => entry.key.startsWith(prefix))
    if (matches.length) await this.deleteEntries(matches)
  }

  async stats(): Promise<{ entries: number, bytes: number }> {
    const entries = await this.entries()
    return { entries: entries.length, bytes: entries.reduce((sum, entry) => sum + entry.bytes, 0) }
  }

  close(): void { this.generation += 1; this.db.close() }

  private async put(entry: Entry): Promise<void> {
    const transaction = this.db.transaction(STORE, 'readwrite')
    try { transaction.objectStore(STORE).put(entry) } catch (error) { transaction.abort(); throw error }
    await transactionDone(transaction)
  }

  private async entries(): Promise<Entry[]> {
    const transaction = this.db.transaction(STORE, 'readonly')
    const entries = await requestResult(transaction.objectStore(STORE).index('scope').getAll(this.scope)) as Entry[]
    await transactionDone(transaction)
    return entries
  }


  private async allEntries(): Promise<Entry[]> {
    const transaction = this.db.transaction(STORE, 'readonly')
    const entries = await requestResult(transaction.objectStore(STORE).getAll()) as Entry[]
    await transactionDone(transaction)
    return entries
  }

  private async globalStats(): Promise<{ entries: number, bytes: number }> {
    const entries = await this.allEntries()
    return { entries: entries.length, bytes: entries.reduce((sum, entry) => sum + entry.bytes, 0) }
  }

  private async purgeOtherScopes(): Promise<void> {
    const staleScopes = (await this.allEntries()).filter(entry => entry.scope !== this.scope)
    if (staleScopes.length) await this.deleteEntries(staleScopes)
  }

  private async evictOldest(count: number): Promise<void> {
    const entries = (await this.allEntries()).sort((a, b) => a.accessedAt - b.accessedAt)
    const transaction = this.db.transaction(STORE, 'readwrite')
    const store = transaction.objectStore(STORE)
    for (const entry of entries.slice(0, count)) store.delete(entry.id)
    await transactionDone(transaction)
  }

  private async prune(): Promise<void> {
    const now = Date.now()
    let entries = (await this.allEntries()).sort((a, b) => a.accessedAt - b.accessedAt)
    const expired = entries.filter(entry => now - entry.updatedAt >= this.policy.staleMs)
    if (expired.length) await this.deleteEntries(expired)
    entries = entries.filter(entry => !expired.includes(entry))
    let bytes = entries.reduce((sum, entry) => sum + entry.bytes, 0)
    const evicted: Entry[] = []
    while (entries.length > this.policy.maxEntries || bytes > this.policy.maxBytes) {
      const entry = entries.shift()
      if (!entry) break
      bytes -= entry.bytes
      evicted.push(entry)
    }
    if (evicted.length) await this.deleteEntries(evicted)
  }

  private async deleteEntries(entries: Entry[]): Promise<void> {
    const transaction = this.db.transaction(STORE, 'readwrite')
    const store = transaction.objectStore(STORE)
    entries.forEach(entry => store.delete(entry.id))
    await transactionDone(transaction)
  }
}
