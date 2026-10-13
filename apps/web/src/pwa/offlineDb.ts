import type {
  OfflineAction,
  OfflineCleanupOptions,
  OfflineMedia,
  OfflineScope,
} from './offlineTypes'
import { desktopRuntime, sealDesktopJson, unsealDesktopJson } from './desktopRuntime'

export const OFFLINE_DATABASE_NAME = 'robopark-offline'
const DEFAULT_OFFLINE_BUDGET = 64 * 1024 * 1024
const MIN_OFFLINE_BUDGET = 256 * 1024
const MAX_OFFLINE_BUDGET = 128 * 1024 * 1024
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
type ActionRecord = ScopedRecord & Partial<OfflineAction> & {
  resource: string
  id: string
  state: OfflineAction['state']
  createdAt: number
  updatedAt: number
  baseRevision?: string | null
  dependencyIds?: string[]
  protectedData?: unknown
}
type MediaRecord = ScopedRecord & Partial<OfflineMedia> & {
  id: string
  actionId: string
  state: OfflineMedia['state']
  createdAt: number
  updatedAt: number
  sizeBytes: number
  blob?: Blob
  originalBlob?: Blob
  protectedData?: unknown
}
type RevisionRecord = ScopedRecord & { section: string, revision: string, updatedAt: number }
type MetaRecord = ScopedRecord & { key: string, value: unknown, updatedAt: number }
type PendingWrite =
  | { kind: 'entity'; key: string; data: unknown; options: { updatedAt?: number; accessedAt?: number } }
  | { kind: 'action'; action: OfflineAction }
  | { kind: 'media'; media: OfflineMedia }
  | { kind: 'deleteMedia'; id: string }
  | { kind: 'revision'; section: string; revision: string }
type PreparedWrite =
  | { kind: 'put'; store: 'entities' | 'actions' | 'media' | 'revisions'; value: EntityRecord | ActionRecord | MediaRecord | RevisionRecord }
  | { kind: 'delete'; store: 'media'; id: string }

let activeScope = ''
let activeScopeValue: OfflineScope | null = null
let activeGeneration = 0
const handles = new Set<OfflineDb>()
const retiredScopeKeys = new Set<string>()

export async function estimateOfflineBudget(
  estimate: () => Promise<{ quota?: number, usage?: number }> = () => navigator.storage.estimate(),
): Promise<number> {
  try {
    const { quota, usage = 0 } = await estimate()
    if (!quota || !Number.isFinite(quota)) return DEFAULT_OFFLINE_BUDGET
    const available = Math.max(0, quota - usage)
    if (available <= MIN_OFFLINE_BUDGET) return Math.floor(available)
    return Math.max(MIN_OFFLINE_BUDGET, Math.min(MAX_OFFLINE_BUDGET, Math.floor(quota * 0.1), Math.floor(available * 0.5)))
  } catch {
    return DEFAULT_OFFLINE_BUDGET
  }
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

function scopeKey(scope: OfflineScope): string {
  return ['v2', scope.account, scope.principal ?? '', scope.role, scope.permissions, scope.park, scope.parkAccess ?? '', String(scope.schema)]
    .map(encodeURIComponent).join('|')
}

function recordId(scope: string, id: string): string { return `${scope}\0${id}` }

function valueBytes(value: unknown): number {
  if (value instanceof Blob) return value.size
  try { return new TextEncoder().encode(JSON.stringify(value)).byteLength } catch { return 0 }
}

async function blobBase64(blob: Blob): Promise<string> {
  const bytes = new Uint8Array(await blob.arrayBuffer())
  let binary = ''
  const chunk = 0x8000
  for (let index = 0; index < bytes.length; index += chunk) {
    binary += String.fromCharCode(...bytes.subarray(index, Math.min(bytes.length, index + chunk)))
  }
  return btoa(binary)
}

function base64Blob(value: string, type: string): Blob {
  if (!/^[A-Za-z0-9+/]*={0,2}$/.test(value)) throw new Error('desktop_media_invalid')
  const binary = atob(value)
  const bytes = new Uint8Array(new ArrayBuffer(binary.length))
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index)
  return new Blob([bytes], { type })
}

function queuedActionMatches(left: OfflineAction, right: OfflineAction): boolean {
  const canonical = (value: unknown): string => {
    const normalize = (entry: unknown): unknown => {
      if (Array.isArray(entry)) return entry.map(normalize)
      if (entry && typeof entry === 'object') {
        return Object.fromEntries(Object.entries(entry).sort(([a], [b]) => a.localeCompare(b))
          .filter(([, item]) => item !== undefined).map(([key, item]) => [key, normalize(item)]))
      }
      return entry
    }
    return JSON.stringify(normalize(value)) ?? 'null'
  }
  return left.id === right.id && left.deviceId === right.deviceId
    && left.resourceType === right.resourceType && left.resourceId === right.resourceId
    && left.action === right.action && left.idempotencyKey === right.idempotencyKey
    && left.baseRevision === right.baseRevision
    && canonical(left.dependencies) === canonical(right.dependencies)
    && canonical(left.payload) === canonical(right.payload)
}

const actionStates = new Set<OfflineAction['state']>(['local', 'ready', 'sending', 'confirmed', 'conflict', 'attention', 'cancelled'])
const mediaStates = new Set<OfflineMedia['state']>(['local', 'ready', 'uploading', 'confirmed', 'attention'])
const injectedActionFields = ['deviceId', 'resourceType', 'resourceId', 'action', 'idempotencyKey', 'dependencies', 'payload', 'result', 'attempts'] as const
const injectedMediaFields = ['issueKey', 'name', 'blob', 'originalBlob', 'mimeType', 'sha256', 'attempts'] as const

function validAction(value: unknown): value is OfflineAction {
  const item = value as Partial<OfflineAction> | null
  return Boolean(item && typeof item.id === 'string' && item.id.length > 0
    && typeof item.deviceId === 'string' && typeof item.resourceType === 'string' && typeof item.resourceId === 'string'
    && typeof item.action === 'string' && typeof item.idempotencyKey === 'string'
    && (item.baseRevision === null || typeof item.baseRevision === 'string')
    && Array.isArray(item.dependencies) && item.dependencies.every(dependency => typeof dependency === 'string')
    && Object.prototype.hasOwnProperty.call(item, 'payload') && actionStates.has(item.state as OfflineAction['state'])
    && Number.isFinite(item.createdAt) && Number.isFinite(item.updatedAt)
    && (item.attempts === undefined || Number.isSafeInteger(item.attempts)))
}

type ProtectedMedia = Omit<OfflineMedia, 'blob' | 'originalBlob'> & { blob: string; originalBlob: string | null }

function validProtectedMedia(value: unknown): value is ProtectedMedia {
  const item = value as Partial<ProtectedMedia> | null
  return Boolean(item && typeof item.id === 'string' && item.id.length > 0 && typeof item.actionId === 'string'
    && typeof item.issueKey === 'string' && typeof item.name === 'string' && typeof item.mimeType === 'string'
    && typeof item.sha256 === 'string' && typeof item.blob === 'string'
    && (item.originalBlob === null || typeof item.originalBlob === 'string')
    && Number.isFinite(item.sizeBytes) && mediaStates.has(item.state as OfflineMedia['state'])
    && Number.isFinite(item.createdAt) && Number.isFinite(item.updatedAt)
    && (item.attempts === undefined || Number.isSafeInteger(item.attempts)))
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
  deleteMedia(id: string): void
  putRevision(section: string, revision: string): void
}
export type SyncTransactionWriter = Pick<OfflineTransactionWriter, 'putAction' | 'putMedia' | 'deleteMedia' | 'putRevision'>

export class OfflineDb {
  private closed = false
  private readonly db: IDBDatabase
  private readonly scope: string
  private readonly generation: number

  constructor(
    db: IDBDatabase,
    scope: string,
    generation: number,
  ) {
    this.db = db
    this.scope = scope
    this.generation = generation
    handles.add(this)
  }

  captureGeneration(): number { return this.generation }
  isGenerationCurrent(generation = this.generation): boolean {
    return !this.closed && generation === this.generation && generation === activeGeneration && this.scope === activeScope
  }

  storeNames(): string[] { return Array.from(this.db.objectStoreNames).sort() }
  indexNames(store: StoreName): string[] {
    const transaction = this.db.transaction(store, 'readonly')
    return Array.from(transaction.objectStore(store).indexNames).sort()
  }

  private collectWrites(mutator: (writer: OfflineTransactionWriter) => unknown): PendingWrite[] {
    const writes: PendingWrite[] = []
    const result = mutator({
      putEntity: (key, data, options = {}) => { writes.push({ kind: 'entity', key, data, options }) },
      putAction: action => { writes.push({ kind: 'action', action }) },
      putMedia: media => { writes.push({ kind: 'media', media }) },
      deleteMedia: id => { writes.push({ kind: 'deleteMedia', id }) },
      putRevision: (section, revision) => { writes.push({ kind: 'revision', section, revision }) },
    })
    if (result && typeof (result as unknown as Promise<unknown>).then === 'function') {
      throw new TypeError('Offline transaction mutator must be synchronous')
    }
    return writes
  }

  private async prepareWrites(writes: PendingWrite[]): Promise<PreparedWrite[]> {
    return Promise.all(writes.map(async write => {
      if (write.kind === 'deleteMedia') return { kind: 'delete', store: 'media', id: write.id } as const
      if (write.kind === 'action') return { kind: 'put', store: 'actions', value: await this.actionRecord(write.action) } as const
      if (write.kind === 'media') return { kind: 'put', store: 'media', value: await this.mediaRecord(write.media) } as const
      if (write.kind === 'revision') {
        const value: RevisionRecord = { dbId: recordId(this.scope, write.section), scope: this.scope, section: write.section, revision: write.revision, bytes: valueBytes(write.revision), updatedAt: Date.now() }
        return { kind: 'put', store: 'revisions', value } as const
      }
      const now = write.options.updatedAt ?? Date.now()
      const data = await sealDesktopJson(write.data, { purpose: `offline-entity:${this.scope}:${write.key}` })
      const value: EntityRecord = { dbId: recordId(this.scope, write.key), scope: this.scope, key: write.key, data, bytes: valueBytes(data), updatedAt: now, accessedAt: write.options.accessedAt ?? now }
      return { kind: 'put', store: 'entities', value } as const
    }))
  }

  private applyWrites(transaction: IDBTransaction, writes: PreparedWrite[]): void {
    for (const write of writes) {
      if (write.kind === 'delete') transaction.objectStore(write.store).delete(recordId(this.scope, write.id))
      else transaction.objectStore(write.store).put(write.value)
    }
  }

  async transaction(mutator: (writer: OfflineTransactionWriter) => unknown): Promise<void> {
    if (!this.isGenerationCurrent()) throw new Error('Offline scope is no longer active')
    const writes = await this.prepareWrites(this.collectWrites(mutator))
    if (!this.isGenerationCurrent()) throw new Error('Offline scope is no longer active')
    const transaction = this.db.transaction(['entities', 'actions', 'media', 'revisions'], 'readwrite')
    this.applyWrites(transaction, writes)
    await transactionDone(transaction)
  }

  async transactionIfLease(owner: string, now: () => number, mutator: (writer: SyncTransactionWriter) => unknown): Promise<boolean> {
    if (!this.isGenerationCurrent()) return false
    const writes = await this.prepareWrites(this.collectWrites(mutator))
    if (!this.isGenerationCurrent()) return false
    const transaction = this.db.transaction(['meta', 'actions', 'media', 'revisions'], 'readwrite')
    const leaseRecord = await requestResult(transaction.objectStore('meta').get(recordId(this.scope, 'sync-lease'))) as MetaRecord | undefined
    const lease = leaseRecord?.value as { owner?: string, until?: number } | undefined
    if (lease?.owner !== owner || !(Number(lease.until) > now())) {
      await transactionDone(transaction)
      return false
    }
    this.applyWrites(transaction, writes)
    await transactionDone(transaction)
    return true
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
    try { return await unsealDesktopJson(record.data, { purpose: `offline-entity:${this.scope}:${key}` }) as T }
    catch (error) {
      if (error instanceof Error && error.message === 'desktop_storage_key_unavailable') return undefined
      await this.deleteRecord('entities', key).catch(() => undefined)
      return undefined
    }
  }

  async putAction(action: OfflineAction): Promise<boolean> {
    if (!this.isGenerationCurrent()) return false
    await this.transaction(writer => writer.putAction(action))
    return this.isGenerationCurrent()
  }
  async putQueuedAction(action: OfflineAction, media?: OfflineMedia): Promise<OfflineAction | false> {
    if (!this.isGenerationCurrent()) return false
    if (desktopRuntime()) return this.putDesktopQueuedAction(action, media)
    const transaction = this.db.transaction(media ? ['actions', 'meta', 'media'] : ['actions', 'meta'], 'readwrite')
    const actions = transaction.objectStore('actions')
    const meta = transaction.objectStore('meta')
    const existing = await requestResult(actions.get(recordId(this.scope, action.id))) as ActionRecord | undefined
    if (existing) {
      const { dbId: _dbId, scope: _scope, bytes: _bytes, resource: _resource, ...storedRecord } = existing
      const stored = storedRecord as OfflineAction
      if (!queuedActionMatches(stored, action)) {
        await transactionDone(transaction)
        throw new Error('sync_payload_conflict')
      }
      if (media) {
        const mediaStore = transaction.objectStore('media')
        const prior = await requestResult(mediaStore.get(recordId(this.scope, media.id))) as MediaRecord | undefined
        if (prior) {
          if (prior.actionId !== media.actionId || prior.issueKey !== media.issueKey
            || prior.name !== media.name || prior.mimeType !== media.mimeType
            || prior.sha256 !== media.sha256 || prior.sizeBytes !== media.sizeBytes) {
            await transactionDone(transaction)
            throw new Error('sync_payload_conflict')
          }
        } else if (!['confirmed', 'cancelled'].includes(stored.state)) {
          mediaStore.put(this.plainMediaRecord(media))
        }
      }
      await transactionDone(transaction)
      return this.isGenerationCurrent() ? stored : false
    } else {
      const key = 'action-order'
      const id = recordId(this.scope, key)
      const saved = await requestResult(meta.get(id)) as MetaRecord | undefined
      let latest = Number(saved?.value)
      if (!Number.isFinite(latest)) {
        const records = await requestResult(actions.index('scope').getAll(this.scope)) as ActionRecord[]
        latest = records.reduce((max, item) => Math.max(max, item.createdAt), Number.NEGATIVE_INFINITY)
      }
      const createdAt = Math.max(action.createdAt, latest + 1)
      action = { ...action, createdAt }
      meta.put({ dbId: id, scope: this.scope, key, value: createdAt, bytes: valueBytes(createdAt), updatedAt: Date.now() } satisfies MetaRecord)
    }
    actions.put(this.plainActionRecord(action))
    if (media) transaction.objectStore('media').put(this.plainMediaRecord(media))
    await transactionDone(transaction)
    return this.isGenerationCurrent() ? action : false
  }

  private async putDesktopQueuedAction(action: OfflineAction, media?: OfflineMedia): Promise<OfflineAction | false> {
    const prior = await this.getRecord<ActionRecord>('actions', action.id)
    if (prior) return this.handleExistingDesktopAction(prior, action, media)
    const reservation = await this.reserveDesktopActionOrder(action.id, action.createdAt)
    if (reservation.existing) return this.handleExistingDesktopAction(reservation.existing, action, media)
    action = { ...action, createdAt: reservation.createdAt }
    const [actionRecord, mediaRecord] = await Promise.all([this.actionRecord(action), media ? this.mediaRecord(media) : Promise.resolve(undefined)])
    if (!this.isGenerationCurrent()) return false
    const transaction = this.db.transaction(mediaRecord ? ['actions', 'media'] : ['actions'], 'readwrite')
    const actions = transaction.objectStore('actions')
    const raced = await requestResult(actions.get(recordId(this.scope, action.id))) as ActionRecord | undefined
    if (raced) {
      await transactionDone(transaction)
      return this.handleExistingDesktopAction(raced, action, media)
    }
    actions.put(actionRecord)
    if (mediaRecord) transaction.objectStore('media').put(mediaRecord)
    await transactionDone(transaction)
    return this.isGenerationCurrent() ? action : false
  }

  private async reserveDesktopActionOrder(id: string, requested: number): Promise<{ createdAt: number; existing?: ActionRecord }> {
    const transaction = this.db.transaction(['actions', 'meta'], 'readwrite')
    const actions = transaction.objectStore('actions')
    const existing = await requestResult(actions.get(recordId(this.scope, id))) as ActionRecord | undefined
    if (existing) { await transactionDone(transaction); return { createdAt: existing.createdAt, existing } }
    const meta = transaction.objectStore('meta')
    const key = 'action-order'
    const orderId = recordId(this.scope, key)
    const saved = await requestResult(meta.get(orderId)) as MetaRecord | undefined
    let latest = Number(saved?.value)
    if (!Number.isFinite(latest)) {
      const records = await requestResult(actions.index('scope').getAll(this.scope)) as ActionRecord[]
      latest = records.reduce((max, item) => Math.max(max, item.createdAt), Number.NEGATIVE_INFINITY)
    }
    const createdAt = Math.max(requested, latest + 1)
    meta.put({ dbId: orderId, scope: this.scope, key, value: createdAt, bytes: valueBytes(createdAt), updatedAt: Date.now() } satisfies MetaRecord)
    await transactionDone(transaction)
    return { createdAt }
  }

  private async handleExistingDesktopAction(record: ActionRecord, requested: OfflineAction, media?: OfflineMedia): Promise<OfflineAction | false> {
    const stored = await this.decodeAction(record)
    if (!queuedActionMatches(stored, requested)) throw new Error('sync_payload_conflict')
    if (media) {
      const priorMedia = await this.getRecord<MediaRecord>('media', media.id)
      if (priorMedia) {
        const storedMedia = await this.decodeMedia(priorMedia)
        if (storedMedia.actionId !== media.actionId || storedMedia.issueKey !== media.issueKey || storedMedia.name !== media.name
          || storedMedia.mimeType !== media.mimeType || storedMedia.sha256 !== media.sha256 || storedMedia.sizeBytes !== media.sizeBytes) throw new Error('sync_payload_conflict')
      } else if (!['confirmed', 'cancelled'].includes(stored.state)) await this.putMedia(media)
    }
    return this.isGenerationCurrent() ? stored : false
  }
  async getAction(id: string): Promise<OfflineAction | undefined> {
    const record = await this.getRecord<ActionRecord>('actions', id)
    if (!record) return undefined
    try { return await this.decodeAction(record) } catch { return undefined }
  }
  async listActions(): Promise<OfflineAction[]> {
    const records = await this.scopedRecords<ActionRecord>('actions')
    const decoded = await Promise.all(records.map(record => this.decodeAction(record).catch(() => undefined)))
    return decoded.filter((item): item is OfflineAction => item !== undefined)
  }

  async putMedia(media: OfflineMedia): Promise<boolean> {
    if (!this.isGenerationCurrent()) return false
    await this.transaction(writer => writer.putMedia(media))
    return this.isGenerationCurrent()
  }
  async getMedia(id: string): Promise<OfflineMedia | undefined> {
    const record = await this.getRecord<MediaRecord>('media', id)
    if (!record) return undefined
    try { return await this.decodeMedia(record) } catch { return undefined }
  }
  async listMedia(): Promise<OfflineMedia[]> {
    const records = await this.scopedRecords<MediaRecord>('media')
    const decoded = await Promise.all(records.map(record => this.decodeMedia(record).catch(() => undefined)))
    return decoded.filter((item): item is OfflineMedia => item !== undefined)
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

  async claimLease(owner: string, now: number, leaseMs: number): Promise<boolean> {
    if (!this.isGenerationCurrent()) return false
    const transaction = this.db.transaction('meta', 'readwrite')
    const store = transaction.objectStore('meta')
    const id = recordId(this.scope, 'sync-lease')
    const current = await requestResult(store.get(id)) as MetaRecord | undefined
    const lease = current?.value as { owner?: string, until?: number } | undefined
    if (lease?.owner && lease.owner !== owner && Number(lease.until) > now) {
      await transactionDone(transaction)
      return false
    }
    const value = { owner, until: now + leaseMs }
    store.put({ dbId: id, scope: this.scope, key: 'sync-lease', value, bytes: valueBytes(value), updatedAt: now } satisfies MetaRecord)
    await transactionDone(transaction)
    return true
  }

  async renewLease(owner: string, now: number, leaseMs: number): Promise<boolean> {
    if (!this.isGenerationCurrent()) return false
    const transaction = this.db.transaction('meta', 'readwrite')
    const store = transaction.objectStore('meta')
    const id = recordId(this.scope, 'sync-lease')
    const current = await requestResult(store.get(id)) as MetaRecord | undefined
    const lease = current?.value as { owner?: string, until?: number } | undefined
    if (lease?.owner !== owner || Number(lease.until) <= now) {
      await transactionDone(transaction)
      return false
    }
    const value = { owner, until: now + leaseMs }
    store.put({ dbId: id, scope: this.scope, key: 'sync-lease', value, bytes: valueBytes(value), updatedAt: now } satisfies MetaRecord)
    await transactionDone(transaction)
    return true
  }

  async releaseLease(owner: string): Promise<void> {
    if (!this.isGenerationCurrent()) return
    const transaction = this.db.transaction('meta', 'readwrite')
    const store = transaction.objectStore('meta')
    const id = recordId(this.scope, 'sync-lease')
    const current = await requestResult(store.get(id)) as MetaRecord | undefined
    const lease = current?.value as { owner?: string } | undefined
    if (lease?.owner === owner) store.delete(id)
    await transactionDone(transaction)
  }

  async cleanup(options: OfflineCleanupOptions): Promise<void> {
    if (!this.isGenerationCurrent()) throw new Error('Offline scope is no longer active')
    const now = options.now ?? Date.now()
    const confirmedTtl = options.confirmedTtlMs ?? 7 * 24 * 60 * 60 * 1000
    const entityTtl = options.entityTtlMs ?? 14 * 24 * 60 * 60 * 1000
    // Keep eligibility reads and deletion in one transaction: a live tab may
    // refresh a record while another tab runs startup cleanup.
    const transaction = this.db.transaction(['entities', 'actions', 'media'], 'readwrite')
    const done = transactionDone(transaction)
    const removeRecords = (store: StoreName, records: ScopedRecord[]) => {
      const objectStore = transaction.objectStore(store)
      for (const record of records) objectStore.delete(record.dbId)
    }
    try {
      const [entities, actions, media] = await Promise.all([
        requestResult(transaction.objectStore('entities').index('scope').getAll(this.scope)) as Promise<EntityRecord[]>,
        requestResult(transaction.objectStore('actions').index('scope').getAll(this.scope)) as Promise<ActionRecord[]>,
        requestResult(transaction.objectStore('media').index('scope').getAll(this.scope)) as Promise<MediaRecord[]>,
      ])
      const dependenciesOfPendingActions = new Set(actions
        .filter(item => item.state !== 'confirmed' && item.state !== 'cancelled')
        .flatMap(item => item.dependencyIds ?? item.dependencies ?? []))
      const expiredEntities = entities.filter(item => now - item.updatedAt >= entityTtl)
      const expiredActions = actions.filter(item => item.state === 'confirmed'
        && !dependenciesOfPendingActions.has(item.id) && now - item.updatedAt >= confirmedTtl)
      const actionsById = new Map(actions.map(item => [item.id, item]))
      const dependentActionIsTerminal = (item: MediaRecord) => {
        const action = actionsById.get(item.actionId)
        return !action || action.state === 'confirmed' || action.state === 'cancelled'
      }
      const mediaRetentionAnchor = (item: MediaRecord) => Math.max(
        item.updatedAt,
        actionsById.get(item.actionId)?.updatedAt ?? item.updatedAt,
      )
      const expiredMedia = media.filter(item => item.state === 'confirmed'
        && dependentActionIsTerminal(item) && now - mediaRetentionAnchor(item) >= confirmedTtl)
      removeRecords('entities', expiredEntities)
      removeRecords('actions', expiredActions)
      removeRecords('media', expiredMedia)

      const remainingEntities = entities.filter(item => !expiredEntities.includes(item)).sort((a, b) => a.accessedAt - b.accessedAt)
      const remainingActions = actions.filter(item => !expiredActions.includes(item))
      const remainingMedia = media.filter(item => !expiredMedia.includes(item))
      let bytes = [...remainingEntities, ...remainingActions, ...remainingMedia].reduce((sum, item) => sum + item.bytes, 0)
      const evictable = [
        ...remainingEntities.map(item => ({ store: 'entities' as const, item })),
        ...remainingActions.filter(item => (item.state === 'confirmed' || item.state === 'cancelled')
          && !dependenciesOfPendingActions.has(item.id))
          .map(item => ({ store: 'actions' as const, item })),
        ...remainingMedia.filter(item => item.state === 'confirmed' && dependentActionIsTerminal(item)
          && now - mediaRetentionAnchor(item) >= confirmedTtl).map(item => ({ store: 'media' as const, item })),
      ]
      for (const candidate of evictable) {
        if (bytes <= options.maxBytes) break
        removeRecords(candidate.store, [candidate.item])
        bytes -= candidate.item.bytes
      }
      await done
      if (bytes > options.maxBytes) throw new OfflineStorageFullError()
    } catch (error) {
      try { transaction.abort() } catch { /* The transaction may already be complete. */ }
      await done.catch(() => undefined)
      throw error
    }
  }

  close(): void {
    if (this.closed) return
    this.closed = true
    handles.delete(this)
    this.db.close()
  }

  private plainActionRecord(action: OfflineAction): ActionRecord {
    const common = { dbId: recordId(this.scope, action.id), scope: this.scope, resource: `${action.resourceType}:${action.resourceId}`, bytes: valueBytes(action) }
    return { ...action, ...common }
  }

  private async actionRecord(action: OfflineAction): Promise<ActionRecord> {
    if (!desktopRuntime()) return this.plainActionRecord(action)
    const index = {
      id: action.id, state: action.state, createdAt: action.createdAt, updatedAt: action.updatedAt,
      baseRevision: action.baseRevision, dependencyIds: [...action.dependencies],
    }
    const protectedData = await sealDesktopJson(action, { purpose: `offline-action:${this.scope}:${action.id}` })
    const common = { dbId: recordId(this.scope, action.id), scope: this.scope, resource: `${action.resourceType}:${action.resourceId}` }
    return { ...index, ...common, protectedData, bytes: valueBytes(protectedData) + valueBytes(index) }
  }

  private async decodeAction(record: ActionRecord): Promise<OfflineAction> {
    const { dbId: _dbId, scope: _scope, bytes: _bytes, resource: _resource, protectedData, dependencyIds: _dependencyIds, ...plain } = record
    if (!desktopRuntime()) return plain as OfflineAction
    if (injectedActionFields.some(field => Object.prototype.hasOwnProperty.call(record, field))) throw new Error('desktop_action_invalid')
    const protectedAction = await unsealDesktopJson(protectedData, { purpose: `offline-action:${this.scope}:${record.id}` })
    if (!validAction(protectedAction)) throw new Error('desktop_action_invalid')
    if (record.id !== protectedAction.id || record.state !== protectedAction.state
      || record.createdAt !== protectedAction.createdAt || record.updatedAt !== protectedAction.updatedAt
      || record.baseRevision !== protectedAction.baseRevision
      || JSON.stringify(record.dependencyIds) !== JSON.stringify(protectedAction.dependencies)
      || record.resource !== `${protectedAction.resourceType}:${protectedAction.resourceId}`) throw new Error('desktop_action_invalid')
    return protectedAction
  }

  private plainMediaRecord(media: OfflineMedia): MediaRecord {
    const bytes = media.blob.size + (media.originalBlob?.size ?? 0) + valueBytes({ ...media, blob: null, originalBlob: null })
    return { ...media, dbId: recordId(this.scope, media.id), scope: this.scope, bytes }
  }

  private async mediaRecord(media: OfflineMedia): Promise<MediaRecord> {
    if (!desktopRuntime()) return this.plainMediaRecord(media)
    const protectedData = await sealDesktopJson({ ...media,
      blob: await blobBase64(media.blob),
      originalBlob: media.originalBlob ? await blobBase64(media.originalBlob) : null,
    }, { purpose: `offline-media:${this.scope}:${media.id}` })
    const index = {
      id: media.id, actionId: media.actionId, state: media.state, sizeBytes: media.sizeBytes,
      createdAt: media.createdAt, updatedAt: media.updatedAt,
    }
    return { ...index, dbId: recordId(this.scope, media.id), scope: this.scope, bytes: valueBytes(protectedData) + valueBytes(index), protectedData }
  }

  private async decodeMedia(record: MediaRecord): Promise<OfflineMedia> {
    const { dbId: _dbId, scope: _scope, bytes: _bytes, protectedData, ...plain } = record
    if (!desktopRuntime()) return plain as OfflineMedia
    if (injectedMediaFields.some(field => Object.prototype.hasOwnProperty.call(record, field))) throw new Error('desktop_media_invalid')
    const protectedMedia = await unsealDesktopJson(protectedData, { purpose: `offline-media:${this.scope}:${record.id}` })
    if (!validProtectedMedia(protectedMedia)) throw new Error('desktop_media_invalid')
    if (record.id !== protectedMedia.id || record.actionId !== protectedMedia.actionId || record.state !== protectedMedia.state
      || record.sizeBytes !== protectedMedia.sizeBytes || record.createdAt !== protectedMedia.createdAt
      || record.updatedAt !== protectedMedia.updatedAt) throw new Error('desktop_media_invalid')
    const blob = base64Blob(protectedMedia.blob, protectedMedia.mimeType)
    if (blob.size !== protectedMedia.sizeBytes) throw new Error('desktop_media_invalid')
    const { originalBlob, ...metadata } = protectedMedia
    return {
      ...metadata, blob,
      ...(typeof originalBlob === 'string' ? { originalBlob: base64Blob(originalBlob, protectedMedia.mimeType) } : {}),
    }
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
}

function scopeIdentity(scope: string): { base: string, schema: number, legacy: boolean } {
  const parts = scope.split('|')
  if (parts[0] === 'v2') return { base: parts.slice(0, 7).join('|'), schema: Number(parts[7]), legacy: false }
  return { base: parts.slice(0, 4).join('|'), schema: Number(parts[4]), legacy: true }
}

function legacyBaseOf(scope: string): string {
  const parts = scope.split('|')
  return [parts[1], parts[3], parts[4], parts[5]].join('|')
}

function mayMigrate(oldScope: string, nextScope: string): boolean {
  const old = scopeIdentity(oldScope)
  const next = scopeIdentity(nextScope)
  if (!(old.schema < next.schema)) return false
  if (!old.legacy) return old.base === next.base
  // The previous format did not record principal or park access. Such records
  // stay durable but quarantined: assigning them to a new identity is unsafe.
  const parts = nextScope.split('|')
  return parts[2] === '' && parts[6] === '' && old.base === legacyBaseOf(nextScope)
}

function sourceSupersedes(storeName: StoreName, source: ScopedRecord, destination: ScopedRecord): boolean {
  const old = source as ScopedRecord & { updatedAt?: number, state?: string, baseRevision?: string | null, revision?: string }
  const current = destination as typeof old
  if (storeName === 'actions' || storeName === 'media') {
    // Final outcomes dominate paused work, which dominates sendable copies.
    // A final destination also wins ties against an older schema's outcome.
    const migrationRank = (state?: string) => {
      if (state === 'confirmed' || state === 'cancelled') return 2
      if (state === 'conflict' || state === 'attention') return 1
      return 0
    }
    const sourceRank = migrationRank(old.state)
    const destinationRank = migrationRank(current.state)
    if (sourceRank !== destinationRank) return sourceRank > destinationRank
    if (destinationRank === 2) return false
    if (storeName === 'actions' && old.baseRevision !== current.baseRevision) return false
  }
  if (storeName === 'revisions' && old.revision !== current.revision) return false
  if (storeName === 'meta') return false
  return typeof old.updatedAt === 'number' && typeof current.updatedAt === 'number' && old.updatedAt > current.updatedAt
}

async function migrateOlderSchema(db: IDBDatabase, scope: string): Promise<void> {
  for (const storeName of STORES) {
    const transaction = db.transaction(storeName, 'readwrite')
    const store = transaction.objectStore(storeName)
    const records = await requestResult(store.getAll()) as ScopedRecord[]
    const byId = new Map(records.map(record => [record.dbId, record]))
    for (const record of records) {
      if (record.scope === scope) continue
      if (!mayMigrate(record.scope, scope)) continue
      const destinationId = recordId(scope, record.dbId.slice(record.scope.length + 1))
      const destination = byId.get(destinationId)
      if (!destination || sourceSupersedes(storeName, record, destination)) {
        const migrated = { ...record, scope, dbId: destinationId }
        store.put(migrated)
        byId.set(destinationId, migrated)
      }
      // The destination represents the same logical record. Tombstone the
      // losing lower-schema copy in this transaction so cleanup cannot revive it.
      store.delete(record.dbId)
    }
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
  await migrateOlderSchema(db, nextScope)
  if (activeScope === nextScope) activeScopeValue = { ...scope }
  retiredScopeKeys.delete(nextScope)
  return new OfflineDb(db, nextScope, activeGeneration)
}

export function activeOfflineScope(): OfflineScope | null { return activeScopeValue ? { ...activeScopeValue } : null }
export function isOfflineScopeRetired(scope: OfflineScope): boolean { return retiredScopeKeys.has(scopeKey(scope)) }

export async function purgeOfflineScope(): Promise<void> {
  if (activeScope) retiredScopeKeys.add(activeScope)
  activeScope = ''
  activeScopeValue = null
  activeGeneration += 1
  for (const handle of [...handles]) handle.close()
}

export function retireOfflineScope(scope: OfflineScope): void {
  const key = scopeKey(scope)
  retiredScopeKeys.add(key)
  if (key !== activeScope) return
  activeScope = ''
  activeScopeValue = null
  activeGeneration += 1
  for (const handle of [...handles]) handle.close()
}
