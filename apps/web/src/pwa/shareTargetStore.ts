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

export function notifyOtherTabsAuthChanged(phase: 'invalidated' | 'verified' = 'invalidated'): void {
  try { localStorage.setItem(AUTH_TRANSITION_KEY, JSON.stringify({ nonce: crypto.randomUUID(), phase })) } catch { /* Storage can be disabled. */ }
}

export function isAuthTransitionStorageKey(key: string | null): boolean {
  return key === AUTH_TRANSITION_KEY
}

function retainedDraftIds(drafts: ShareDraft[], now: number): Set<string> {
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
      const result = await requestResult(transaction.objectStore(STORE).getAll()) as ShareDraft[]
      await transactionDone(transaction)
      const keepIds = retainedDraftIds(result, Date.now())
      if (keepIds.size !== result.length) {
        const cleanup = db.transaction(STORE, 'readwrite')
        for (const item of result) if (!keepIds.has(item.id)) cleanup.objectStore(STORE).delete(item.id)
        await transactionDone(cleanup)
      }
      return result.filter(item => keepIds.has(item.id) && item.ownerAccountId === accountId)
        .sort((left, right) => left.createdAt - right.createdAt)
    },
    async save(draft) {
      const transaction = db.transaction(STORE, 'readwrite')
      transaction.objectStore(STORE).put(draft)
      await transactionDone(transaction)
    },
    async claim(id, accountId) {
      const transaction = db.transaction(STORE, 'readwrite')
      const store = transaction.objectStore(STORE)
      const drafts = await requestResult(store.getAll()) as ShareDraft[]
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
      return claimed
    },
    async assign(accountId, id, assignment) {
      const transaction = db.transaction(STORE, 'readwrite')
      const store = transaction.objectStore(STORE)
      const draft = await requestResult(store.get(id)) as ShareDraft | undefined
      if (draft?.ownerAccountId === accountId) store.put({ ...draft, assignment })
      await transactionDone(transaction)
    },
    async discard(accountId, id) {
      const transaction = db.transaction(STORE, 'readwrite')
      const store = transaction.objectStore(STORE)
      const draft = await requestResult(store.get(id)) as ShareDraft | undefined
      if (draft?.ownerAccountId === accountId) store.delete(id)
      await transactionDone(transaction)
    },
    async clear() {
      const transaction = db.transaction(STORE, 'readwrite')
      transaction.objectStore(STORE).clear()
      await transactionDone(transaction)
    },
    async clearForAccount(accountId) {
      const transaction = db.transaction(STORE, 'readwrite')
      const store = transaction.objectStore(STORE)
      const drafts = await requestResult(store.getAll()) as ShareDraft[]
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
