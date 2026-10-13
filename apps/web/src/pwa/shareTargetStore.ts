import { desktopRuntime, sealDesktopJson, unsealDesktopJson } from './desktopRuntime'

export type ShareDraftAssignment = { kind: 'task', target: string } | { kind: 'report', target: null }
export type ShareDraft = {
  id: string
  createdAt: number
  name: string
  type: string
  blob: Blob
  assignment: ShareDraftAssignment | null
  ownerAccountId?: number | null
  receivedOrder?: number
}

export type ShareTargetStore = {
  list(accountId: number): Promise<ShareDraft[]>
  save(draft: ShareDraft): Promise<void>
  claim(id: string, accountId: number): Promise<ShareDraft | null>
  assign(accountId: number, id: string, assignment: ShareDraftAssignment): Promise<void>
  discard(accountId: number, id: string): Promise<void>
  clear(): Promise<void>
  clearForAccount(accountId: number): Promise<void>
  close?(): void
}

const DB_NAME = 'robopark-share-inbox-v2'
const STORE = 'drafts'
const DRAFT_TTL_MS = 24 * 60 * 60 * 1000
const MAX_DRAFTS = 10
const AUTH_TRANSITION_KEY = 'robopark:auth-transition'
type StoredShareDraft = Partial<ShareDraft> & {
  id: string
  createdAt: number
  ownerAccountId?: number | null
  receivedOrder?: number
  name?: string
  type?: string
  blob?: Blob
  blobBytes?: number
  protectedData?: unknown
}
const injectedDraftFields = ['name', 'type', 'blob', 'assignment'] as const

async function blobBase64(blob: Blob): Promise<string> {
  const bytes = new Uint8Array(await blob.arrayBuffer())
  let binary = ''
  for (let offset = 0; offset < bytes.length; offset += 0x8000) binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000))
  return btoa(binary)
}

function base64Blob(value: string, type: string): Blob {
  const binary = atob(value)
  const bytes = new Uint8Array(binary.length)
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index)
  return new Blob([bytes], { type })
}

async function storedDraft(draft: ShareDraft): Promise<StoredShareDraft> {
  if (!desktopRuntime()) return draft
  return {
    id: draft.id, createdAt: draft.createdAt, ownerAccountId: draft.ownerAccountId,
    receivedOrder: draft.receivedOrder, blobBytes: draft.blob.size,
    protectedData: await sealDesktopJson({ ...draft, blob: await blobBase64(draft.blob) }, { purpose: `share-draft:${draft.id}` }),
  }
}

async function restoredDraft(draft: StoredShareDraft): Promise<ShareDraft | null> {
  if (!desktopRuntime()) return draft as ShareDraft
  if (!draft.protectedData || injectedDraftFields.some(field => Object.prototype.hasOwnProperty.call(draft, field))) return null
  try {
    const data = await unsealDesktopJson(draft.protectedData, { purpose: `share-draft:${draft.id}` }) as {
      id?: unknown; createdAt?: unknown; name?: unknown; type?: unknown; blob?: unknown
      assignment?: unknown; ownerAccountId?: unknown; receivedOrder?: unknown
    }
    const assignment = data.assignment
    const validAssignment = assignment === null || !!assignment && typeof assignment === 'object'
      && ((assignment as ShareDraftAssignment).kind === 'task' && typeof (assignment as ShareDraftAssignment).target === 'string'
        || (assignment as ShareDraftAssignment).kind === 'report' && (assignment as ShareDraftAssignment).target === null)
    const validOwner = data.ownerAccountId === undefined || data.ownerAccountId === null || Number.isSafeInteger(data.ownerAccountId)
    const validOrder = data.receivedOrder === undefined || typeof data.receivedOrder === 'number' && Number.isFinite(data.receivedOrder)
    if (data.id !== draft.id || data.createdAt !== draft.createdAt || data.ownerAccountId !== draft.ownerAccountId
      || data.receivedOrder !== draft.receivedOrder || typeof data.createdAt !== 'number' || !Number.isFinite(data.createdAt)
      || typeof data.name !== 'string' || typeof data.type !== 'string' || typeof data.blob !== 'string'
      || !validAssignment || !validOwner || !validOrder) return null
    const blob = base64Blob(data.blob, data.type)
    if (blob.size !== draft.blobBytes) return null
    return {
      id: data.id, createdAt: data.createdAt, name: data.name, type: data.type, blob,
      assignment: assignment as ShareDraftAssignment | null,
      ...(data.ownerAccountId !== undefined ? { ownerAccountId: data.ownerAccountId as number | null } : {}),
      ...(data.receivedOrder !== undefined ? { receivedOrder: data.receivedOrder as number } : {}),
    }
  } catch { return null }
}

function sameProtectedDraft(left: StoredShareDraft, right: StoredShareDraft): boolean {
  return left.id === right.id && left.createdAt === right.createdAt
    && left.ownerAccountId === right.ownerAccountId && left.receivedOrder === right.receivedOrder
    && left.blobBytes === right.blobBytes
    && JSON.stringify(left.protectedData) === JSON.stringify(right.protectedData)
}

export function notifyOtherTabsAuthChanged(phase: 'invalidated' | 'verified' = 'invalidated'): void {
  try { localStorage.setItem(AUTH_TRANSITION_KEY, JSON.stringify({ nonce: crypto.randomUUID(), phase })) } catch { /* Storage can be disabled. */ }
}

export function isAuthTransitionStorageKey(key: string | null): boolean {
  return key === AUTH_TRANSITION_KEY
}

function retainedDraftIds(drafts: StoredShareDraft[], now: number): Set<string> {
  const sorted = [...drafts].sort((left, right) => right.createdAt - left.createdAt || (right.receivedOrder ?? 0) - (left.receivedOrder ?? 0))
  const counts = new Map<number | null, number>()
  const keep = new Set<string>()
  for (const draft of sorted) {
    if (draft.createdAt < now - DRAFT_TTL_MS) continue
    const owner = draft.ownerAccountId ?? null
    const count = counts.get(owner) ?? 0
    if (count >= MAX_DRAFTS) continue
    keep.add(draft.id)
    counts.set(owner, count + 1)
  }
  return keep
}

function requestResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

function transactionDone(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve()
    transaction.onerror = () => reject(transaction.error)
    transaction.onabort = () => reject(transaction.error)
  })
}

export async function openShareTargetInbox(): Promise<ShareTargetStore> {
  const request = indexedDB.open(DB_NAME, 1)
  request.onupgradeneeded = () => {
    if (!request.result.objectStoreNames.contains(STORE)) request.result.createObjectStore(STORE, { keyPath: 'id' })
  }
  const db = await requestResult(request)
  return {
    async list(accountId) {
      const transaction = db.transaction(STORE, 'readonly')
      const result = await requestResult(transaction.objectStore(STORE).getAll()) as StoredShareDraft[]
      await transactionDone(transaction)
      const keepIds = retainedDraftIds(result, Date.now())
      if (keepIds.size !== result.length) {
        const cleanup = db.transaction(STORE, 'readwrite')
        for (const item of result) if (!keepIds.has(item.id)) cleanup.objectStore(STORE).delete(item.id)
        await transactionDone(cleanup)
      }
      const visible = result.filter(item => keepIds.has(item.id) && item.ownerAccountId === accountId)
        .sort((left, right) => left.createdAt - right.createdAt)
      return (await Promise.all(visible.map(restoredDraft))).filter((item): item is ShareDraft => item !== null)
    },
    async save(draft) {
      const prepared = await storedDraft(draft)
      const transaction = db.transaction(STORE, 'readwrite')
      transaction.objectStore(STORE).put(prepared)
      await transactionDone(transaction)
    },
    async claim(id, accountId) {
      if (desktopRuntime()) {
        const cleanup = db.transaction(STORE, 'readwrite')
        const cleanupStore = cleanup.objectStore(STORE)
        const drafts = await requestResult(cleanupStore.getAll()) as StoredShareDraft[]
        const keepIds = retainedDraftIds(drafts, Date.now())
        for (const item of drafts) if (!keepIds.has(item.id)) cleanupStore.delete(item.id)
        await transactionDone(cleanup)
        const stored = drafts.find(item => item.id === id)
        if (!stored || !keepIds.has(id)) return null
        const draft = await restoredDraft(stored)
        if (!draft || draft.assignment !== null || draft.ownerAccountId != null && draft.ownerAccountId !== accountId) return null
        if (draft.ownerAccountId === accountId) return draft
        const claimed = { ...draft, ownerAccountId: accountId }
        const prepared = await storedDraft(claimed)
        const transaction = db.transaction(STORE, 'readwrite')
        const store = transaction.objectStore(STORE)
        const current = await requestResult(store.get(id)) as StoredShareDraft | undefined
        const unchanged = !!current && sameProtectedDraft(current, stored)
        if (unchanged) store.put(prepared)
        await transactionDone(transaction)
        return unchanged ? claimed : null
      }
      const transaction = db.transaction(STORE, 'readwrite')
      const store = transaction.objectStore(STORE)
      const drafts = await requestResult(store.getAll()) as StoredShareDraft[]
      const keepIds = retainedDraftIds(drafts, Date.now())
      for (const item of drafts) if (!keepIds.has(item.id)) store.delete(item.id)
      const draft = drafts.find(item => item.id === id)
      if (!draft || !keepIds.has(id) || draft.assignment !== null ||
          draft.ownerAccountId != null && draft.ownerAccountId !== accountId) {
        await transactionDone(transaction)
        return null
      }
      const claimed = { ...draft, ownerAccountId: accountId }
      if (draft.ownerAccountId == null) store.put(claimed)
      await transactionDone(transaction)
      return restoredDraft(claimed)
    },
    async assign(accountId, id, assignment) {
      if (desktopRuntime()) {
        const read = db.transaction(STORE, 'readonly')
        const stored = await requestResult(read.objectStore(STORE).get(id)) as StoredShareDraft | undefined
        await transactionDone(read)
        if (!stored) return
        const draft = await restoredDraft(stored)
        if (!draft || draft.ownerAccountId !== accountId) return
        const prepared = await storedDraft({ ...draft, assignment })
        const transaction = db.transaction(STORE, 'readwrite')
        const store = transaction.objectStore(STORE)
        const current = await requestResult(store.get(id)) as StoredShareDraft | undefined
        if (current && sameProtectedDraft(current, stored)) store.put(prepared)
        await transactionDone(transaction)
        return
      }
      const transaction = db.transaction(STORE, 'readwrite')
      const store = transaction.objectStore(STORE)
      const draft = await requestResult(store.get(id)) as StoredShareDraft | undefined
      if (draft?.ownerAccountId === accountId) store.put({ ...draft, assignment })
      await transactionDone(transaction)
    },
    async discard(accountId, id) {
      if (desktopRuntime()) {
        const read = db.transaction(STORE, 'readonly')
        const stored = await requestResult(read.objectStore(STORE).get(id)) as StoredShareDraft | undefined
        await transactionDone(read)
        if (!stored || (await restoredDraft(stored))?.ownerAccountId !== accountId) return
        const transaction = db.transaction(STORE, 'readwrite')
        const store = transaction.objectStore(STORE)
        const current = await requestResult(store.get(id)) as StoredShareDraft | undefined
        if (current && sameProtectedDraft(current, stored)) store.delete(id)
        await transactionDone(transaction)
        return
      }
      const transaction = db.transaction(STORE, 'readwrite')
      const store = transaction.objectStore(STORE)
      const draft = await requestResult(store.get(id)) as StoredShareDraft | undefined
      if (draft?.ownerAccountId === accountId) store.delete(id)
      await transactionDone(transaction)
    },
    async clear() {
      const transaction = db.transaction(STORE, 'readwrite')
      transaction.objectStore(STORE).clear()
      await transactionDone(transaction)
    },
    async clearForAccount(accountId) {
      if (desktopRuntime()) {
        const read = db.transaction(STORE, 'readonly')
        const drafts = await requestResult(read.objectStore(STORE).getAll()) as StoredShareDraft[]
        await transactionDone(read)
        const decoded = await Promise.all(drafts.map(async draft => ({ draft, value: await restoredDraft(draft) })))
        const candidates = decoded.filter(item => item.value?.ownerAccountId === accountId)
        if (candidates.length === 0) return
        const transaction = db.transaction(STORE, 'readwrite')
        const store = transaction.objectStore(STORE)
        const checks = candidates.map(async ({ draft }) => {
          const current = await requestResult(store.get(draft.id)) as StoredShareDraft | undefined
          if (current && sameProtectedDraft(current, draft)) store.delete(draft.id)
        })
        await Promise.all(checks)
        await transactionDone(transaction)
        return
      }
      const transaction = db.transaction(STORE, 'readwrite')
      const store = transaction.objectStore(STORE)
      const drafts = await requestResult(store.getAll()) as StoredShareDraft[]
      for (const draft of drafts) if (draft.ownerAccountId === accountId) store.delete(draft.id)
      await transactionDone(transaction)
    },
    close() { db.close() },
  }
}

export async function clearShareTargetInbox(accountId: number | null): Promise<void> {
  if (typeof indexedDB === 'undefined' || accountId === null) return
  const inbox = await openShareTargetInbox()
  try { await inbox.clearForAccount(accountId) } finally { inbox.close?.() }
}
