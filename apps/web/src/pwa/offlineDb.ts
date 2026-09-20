import type {
  OfflineAction,
  OfflineCleanupOptions,
  OfflineMedia,
  OfflineScope,
} from './offlineTypes'

export const OFFLINE_DATABASE_NAME = 'robopark-offline'
const DATABASE_VERSION = 2
const STORES = ['actions', 'entities', 'media', 'meta', 'revisions'] as const
type StoreName = typeof STORES[number]

type ScopedRecord = { dbId: string, scope: string, bytes: number }
type EntityRecord = ScopedRecord & {
  key: string
  data: unknown
  updatedAt: number
  accessedAt: number
}
type ActionRecord = ScopedRecord & OfflineAction & { resource: string }
type MediaRecord = ScopedRecord & OfflineMedia
type RevisionRecord = ScopedRecord & { section: string, revision: string, updatedAt: number }

let activeScope = ''
let activeGeneration = 0
const handles = new Set<OfflineDb>()

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

function scopeKey(scope: OfflineScope): string {
  return [scope.account, scope.role, scope.permissions, scope.park, String(scope.schema)]
    .map(encodeURIComponent).join('|')
}

function recordId(scope: string, id: string): string { return `${scope}\0${id}` }

function valueBytes(value: unknown): number {
  if (value instanceof Blob) return value.size
  try { return new TextEncoder().encode(JSON.stringify(value)).byteLength } catch { return 0 }
}

function createIndex(store: IDBObjectStore, name: string, keyPath: string): void {
  if (!store.indexNames.contains(name)) store.createIndex(name, keyPath, { unique: false })
}

function upgradeDatabase(request: IDBOpenDBRequest): void {
  const db = request.result
  const transaction = request.transaction
  if (!transaction) throw new Error('IndexedDB upgrade transaction is unavailable')
  const getStore = (name: StoreName) => db.objectStoreNames.contains(name)
    ? transaction.objectStore(name)
    : db.createObjectStore(name, { keyPath: 'dbId' })
  const entities = getStore('entities')
  createIndex(entities, 'scope', 'scope')
  createIndex(entities, 'updatedAt', 'updatedAt')
  const actions = getStore('actions')
  createIndex(actions, 'resource', 'resource')
  createIndex(actions, 'scope', 'scope')
  createIndex(actions, 'state', 'state')
  createIndex(actions, 'updatedAt', 'updatedAt')
  const media = getStore('media')
  createIndex(media, 'action', 'actionId')
  createIndex(media, 'scope', 'scope')
  createIndex(media, 'state', 'state')
  createIndex(getStore('revisions'), 'scope', 'scope')
  createIndex(getStore('meta'), 'scope', 'scope')
}

async function openDatabase(): Promise<IDBDatabase> {
  if (typeof indexedDB === 'undefined') throw new Error('IndexedDB is unavailable')
  const request = indexedDB.open(OFFLINE_DATABASE_NAME, DATABASE_VERSION)
  request.onupgradeneeded = () => upgradeDatabase(request)
  return requestResult(request)
}

export class OfflineStorageFullError extends Error {
  constructor() { super('Недостаточно места. Незавершённые изменения сохранены; освободите память устройства.') }
}

export type OfflineTransactionWriter = {
  putEntity(key: string, data: unknown, options?: { updatedAt?: number, accessedAt?: number }): void
  putAction(action: OfflineAction): void
  putMedia(media: OfflineMedia): void
}

export class OfflineDb {
  private closed = false

  constructor(
    private readonly db: IDBDatabase,
    private readonly scope: string,
    private readonly generation: number,
  ) { handles.add(this) }

  captureGeneration(): number { return this.generation }
  isGenerationCurrent(generation = this.generation): boolean {
    return !this.closed && generation === this.generation && generation === activeGeneration && this.scope === activeScope
  }

  storeNames(): string[] { return Array.from(this.db.objectStoreNames).sort() }
  indexNames(store: StoreName): string[] {
    const transaction = this.db.transaction(store, 'readonly')
    return Array.from(transaction.objectStore(store).indexNames).sort()
  }

  async transaction(mutator: (writer: OfflineTransactionWriter) => void): Promise<void> {
    if (!this.isGenerationCurrent()) throw new Error('Offline scope is no longer active')
    const transaction = this.db.transaction(['entities', 'actions', 'media'], 'readwrite')
    const writer: OfflineTransactionWriter = {
      putEntity: (key, data, options = {}) => {
        const now = options.updatedAt ?? Date.now()
        const entry: EntityRecord = {
          dbId: recordId(this.scope, key), scope: this.scope, key, data,
          bytes: valueBytes(data), updatedAt: now, accessedAt: options.accessedAt ?? now,
        }
        transaction.objectStore('entities').put(entry)
      },
      putAction: action => transaction.objectStore('actions').put(this.actionRecord(action)),
      putMedia: media => transaction.objectStore('media').put(this.mediaRecord(media)),
    }
    try {
      const result = mutator(writer)
      if (result && typeof (result as unknown as Promise<unknown>).then === 'function') {
        throw new TypeError('Offline transaction mutator must be synchronous')
      }
    } catch (error) {
      transaction.abort()
      await transactionDone(transaction).catch(() => undefined)
      throw error
    }
    await transactionDone(transaction)
  }

  async putEntity(
    key: string,
    data: unknown,
    options: { generation?: number, updatedAt?: number, accessedAt?: number } = {},
  ): Promise<boolean> {
    if (!this.isGenerationCurrent(options.generation ?? this.generation)) return false
    await this.transaction(writer => writer.putEntity(key, data, options))
    if (!this.isGenerationCurrent(options.generation ?? this.generation)) {
      await this.deleteRecord('entities', key).catch(() => undefined)
      return false
    }
    return true
  }

  async getEntity<T>(key: string): Promise<T | undefined> {
    const record = await this.getRecord<EntityRecord>('entities', key)
    if (!record) return undefined
    if (this.isGenerationCurrent()) {
      record.accessedAt = Date.now()
      const transaction = this.db.transaction('entities', 'readwrite')
      transaction.objectStore('entities').put(record)
      await transactionDone(transaction)
    }
    return record.data as T
  }

  async putAction(action: OfflineAction): Promise<boolean> {
    if (!this.isGenerationCurrent()) return false
    await this.transaction(writer => writer.putAction(action))
    return this.isGenerationCurrent()
  }
  async getAction(id: string): Promise<OfflineAction | undefined> {
    const record = await this.getRecord<ActionRecord>('actions', id)
    if (!record) return undefined
    const { dbId: _dbId, scope: _scope, bytes: _bytes, resource: _resource, ...action } = record
    return action
  }
  async listActions(): Promise<OfflineAction[]> {
    const records = await this.scopedRecords<ActionRecord>('actions')
    return records.map(({ dbId: _dbId, scope: _scope, bytes: _bytes, resource: _resource, ...item }) => item)
  }

  async putMedia(media: OfflineMedia): Promise<boolean> {
    if (!this.isGenerationCurrent()) return false
    await this.transaction(writer => writer.putMedia(media))
    return this.isGenerationCurrent()
  }
  async getMedia(id: string): Promise<OfflineMedia | undefined> {
    const record = await this.getRecord<MediaRecord>('media', id)
    if (!record) return undefined
    const { dbId: _dbId, scope: _scope, bytes: _bytes, ...media } = record
    return media
  }

  async setRevision(section: string, revision: string): Promise<void> {
    if (!this.isGenerationCurrent()) throw new Error('Offline scope is no longer active')
    const transaction = this.db.transaction('revisions', 'readwrite')
    const entry: RevisionRecord = {
      dbId: recordId(this.scope, section), scope: this.scope, section, revision,
      bytes: valueBytes(revision), updatedAt: Date.now(),
    }
    transaction.objectStore('revisions').put(entry)
    await transactionDone(transaction)
  }
  async getRevision(section: string): Promise<string | undefined> {
    return (await this.getRecord<RevisionRecord>('revisions', section))?.revision
  }

  async cleanup(options: OfflineCleanupOptions): Promise<void> {
    const now = options.now ?? Date.now()
    const confirmedTtl = options.confirmedTtlMs ?? 7 * 24 * 60 * 60 * 1000
    const entityTtl = options.entityTtlMs ?? 24 * 60 * 60 * 1000
    const entities = await this.scopedRecords<EntityRecord>('entities')
    const actions = await this.scopedRecords<ActionRecord>('actions')
    const media = await this.scopedRecords<MediaRecord>('media')
    const expiredEntities = entities.filter(item => now - item.updatedAt >= entityTtl)
    const expiredActions = actions.filter(item => item.state === 'confirmed' && now - item.updatedAt >= confirmedTtl)
    const expiredMedia = media.filter(item => item.state === 'confirmed' && now - item.updatedAt >= confirmedTtl)
    await this.deleteRecords('entities', expiredEntities)
    await this.deleteRecords('actions', expiredActions)
    await this.deleteRecords('media', expiredMedia)

    const remainingEntities = entities.filter(item => !expiredEntities.includes(item)).sort((a, b) => a.accessedAt - b.accessedAt)
    const remainingActions = actions.filter(item => !expiredActions.includes(item))
    const remainingMedia = media.filter(item => !expiredMedia.includes(item))
    let bytes = [...remainingEntities, ...remainingActions, ...remainingMedia].reduce((sum, item) => sum + item.bytes, 0)
    const evictable = [
      ...remainingEntities.map(item => ({ store: 'entities' as const, item })),
      ...remainingActions.filter(item => item.state === 'confirmed' || item.state === 'cancelled').map(item => ({ store: 'actions' as const, item })),
      ...remainingMedia.filter(item => item.state === 'confirmed').map(item => ({ store: 'media' as const, item })),
    ]
    for (const candidate of evictable) {
      if (bytes <= options.maxBytes) break
      await this.deleteRecords(candidate.store, [candidate.item])
      bytes -= candidate.item.bytes
    }
    if (bytes > options.maxBytes) throw new OfflineStorageFullError()
  }

  close(): void {
    if (this.closed) return
    this.closed = true
    handles.delete(this)
    this.db.close()
  }

  private actionRecord(action: OfflineAction): ActionRecord {
    return {
      ...action,
      dbId: recordId(this.scope, action.id), scope: this.scope,
      resource: `${action.resourceType}:${action.resourceId}`,
      bytes: valueBytes(action),
    }
  }
  private mediaRecord(media: OfflineMedia): MediaRecord {
    return { ...media, dbId: recordId(this.scope, media.id), scope: this.scope, bytes: media.blob.size + valueBytes({ ...media, blob: null }) }
  }
  private async getRecord<T>(store: StoreName, id: string): Promise<T | undefined> {
    if (!this.isGenerationCurrent()) return undefined
    const transaction = this.db.transaction(store, 'readonly')
    const result = await requestResult(transaction.objectStore(store).get(recordId(this.scope, id))) as T | undefined
    await transactionDone(transaction)
    return result
  }
  private async scopedRecords<T extends ScopedRecord>(store: StoreName): Promise<T[]> {
    const transaction = this.db.transaction(store, 'readonly')
    const result = await requestResult(transaction.objectStore(store).index('scope').getAll(this.scope)) as T[]
    await transactionDone(transaction)
    return result
  }
  private async deleteRecord(store: StoreName, id: string): Promise<void> {
    const transaction = this.db.transaction(store, 'readwrite')
    transaction.objectStore(store).delete(recordId(this.scope, id))
    await transactionDone(transaction)
  }
  private async deleteRecords(store: StoreName, records: ScopedRecord[]): Promise<void> {
    if (!records.length) return
    const transaction = this.db.transaction(store, 'readwrite')
    const objectStore = transaction.objectStore(store)
    records.forEach(record => objectStore.delete(record.dbId))
    await transactionDone(transaction)
  }
}

async function purgeOtherScopes(db: IDBDatabase, scope: string): Promise<void> {
  for (const storeName of STORES) {
    const transaction = db.transaction(storeName, 'readwrite')
    const store = transaction.objectStore(storeName)
    const records = await requestResult(store.getAll()) as ScopedRecord[]
    records.filter(record => record.scope !== scope).forEach(record => store.delete(record.dbId))
    await transactionDone(transaction)
  }
}

export async function openOfflineDb(scope: OfflineScope): Promise<OfflineDb> {
  const nextScope = scopeKey(scope)
  if (nextScope !== activeScope) {
    activeScope = nextScope
    activeGeneration += 1
  }
  const db = await openDatabase()
  await purgeOtherScopes(db, nextScope)
  return new OfflineDb(db, nextScope, activeGeneration)
}

export async function purgeOfflineScope(): Promise<void> {
  activeScope = ''
  activeGeneration += 1
  for (const handle of [...handles]) handle.close()
  if (typeof indexedDB === 'undefined') return
  const request = indexedDB.deleteDatabase(OFFLINE_DATABASE_NAME)
  await requestResult(request).then(() => undefined)
}
