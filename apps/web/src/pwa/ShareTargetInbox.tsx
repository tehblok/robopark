import { useEffect, useState } from 'react'
import { Button } from '../design-system/actions/Button'
import './ShareTargetInbox.css'

export type ShareDraftAssignment = { kind: 'task', target: string } | { kind: 'report', target: null }
export type ShareDraft = {
  id: string
  createdAt: number
  name: string
  type: string
  blob: Blob
  assignment: ShareDraftAssignment | null
}

export type ShareTargetStore = {
  list(): Promise<ShareDraft[]>
  save(draft: ShareDraft): Promise<void>
  assign(id: string, assignment: ShareDraftAssignment): Promise<void>
  discard(id: string): Promise<void>
  clear(): Promise<void>
  close?(): void
}

const DB_NAME = 'robopark-share-inbox'
const STORE = 'drafts'
const DRAFT_TTL_MS = 24 * 60 * 60 * 1000
const MAX_DRAFTS = 10

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
    async list() {
      const transaction = db.transaction(STORE, 'readonly')
      const result = await requestResult(transaction.objectStore(STORE).getAll()) as ShareDraft[]
      await transactionDone(transaction)
      const sorted = result.sort((left, right) => left.createdAt - right.createdAt)
      const keep = sorted.filter(item => item.createdAt >= Date.now() - DRAFT_TTL_MS).slice(-MAX_DRAFTS)
      const keepIds = new Set(keep.map(item => item.id))
      if (keep.length !== result.length) {
        const cleanup = db.transaction(STORE, 'readwrite')
        for (const item of result) if (!keepIds.has(item.id)) cleanup.objectStore(STORE).delete(item.id)
        await transactionDone(cleanup)
      }
      return keep
    },
    async save(draft) {
      const transaction = db.transaction(STORE, 'readwrite')
      transaction.objectStore(STORE).put(draft)
      await transactionDone(transaction)
    },
    async assign(id, assignment) {
      const transaction = db.transaction(STORE, 'readwrite')
      const store = transaction.objectStore(STORE)
      const draft = await requestResult(store.get(id)) as ShareDraft | undefined
      if (draft) store.put({ ...draft, assignment })
      await transactionDone(transaction)
    },
    async discard(id) {
      const transaction = db.transaction(STORE, 'readwrite')
      transaction.objectStore(STORE).delete(id)
      await transactionDone(transaction)
    },
    async clear() {
      const transaction = db.transaction(STORE, 'readwrite')
      transaction.objectStore(STORE).clear()
      await transactionDone(transaction)
    },
    close() { db.close() },
  }
}

export async function clearShareTargetInbox(): Promise<void> {
  if (typeof indexedDB === 'undefined') return
  const inbox = await openShareTargetInbox()
  try { await inbox.clear() } finally { inbox.close?.() }
}

export function ShareTargetInbox({ inbox: providedInbox, onAttachTask, onAttachReport }: {
  inbox?: ShareTargetStore
  onAttachTask?: (taskKey: string, draft: ShareDraft) => Promise<void>
  onAttachReport?: (draft: ShareDraft) => Promise<void>
}) {
  const [inbox, setInbox] = useState<ShareTargetStore | null>(providedInbox ?? null)
  const [drafts, setDrafts] = useState<ShareDraft[]>([])
  const [kind, setKind] = useState<'task' | 'report'>('task')
  const [taskKey, setTaskKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    let owned: ShareTargetStore | null = null
    if (providedInbox) { setInbox(providedInbox); return () => { active = false } }
    if (typeof indexedDB === 'undefined') return () => { active = false }
    void openShareTargetInbox().then(value => { owned = value; if (active) setInbox(value); else value.close?.() }).catch(() => undefined)
    return () => { active = false; owned?.close?.() }
  }, [providedInbox])

  useEffect(() => {
    if (!inbox) return
    let active = true
    void inbox.list().then(items => { if (active) setDrafts(items.filter(item => item.assignment === null)) }).catch(() => undefined)
    return () => { active = false }
  }, [inbox])

  if (!inbox || drafts.length === 0) return null
  const draft = drafts[0]
  const refresh = async () => setDrafts((await inbox.list()).filter(item => item.assignment === null))
  const attach = async () => {
    const target = taskKey.trim().toUpperCase()
    if (kind === 'task' && !target) return
    setBusy(true); setError('')
    try {
      if (kind === 'task' && onAttachTask) {
        await onAttachTask(target, draft)
        await inbox.discard(draft.id)
      } else if (kind === 'report' && onAttachReport) {
        await onAttachReport(draft)
        await inbox.discard(draft.id)
      } else {
        await inbox.assign(draft.id, kind === 'task' ? { kind, target } : { kind, target: null })
      }
      await refresh()
    } catch {
      setError('Не удалось прикрепить фото. Черновик сохранён на устройстве.')
    } finally { setBusy(false) }
  }
  const discard = async () => {
    await inbox.discard(draft.id)
    await refresh()
  }

  return (
    <aside aria-label="Полученное фото" className="rp-share-inbox">
      <div><strong>Получено фото</strong><span>{draft.name}</span></div>
      <label>Куда прикрепить
        <select onChange={event => setKind(event.target.value as 'task' | 'report')} value={kind}>
          <option value="task">К задаче</option><option value="report">К репорту</option>
        </select>
      </label>
      {kind === 'task' ? <label>Номер задачи<input onChange={event => setTaskKey(event.target.value)} placeholder="SDCFLEETOPS-123" value={taskKey} /></label> : null}
      <div className="rp-share-inbox__actions">
        <Button busy={busy} disabled={busy || kind === 'task' && !taskKey.trim()} onClick={() => void attach()} size="compact">Прикрепить</Button>
        <Button aria-label={`Удалить черновик ${draft.name}`} disabled={busy} onClick={() => void discard()} size="compact" variant="ghost">Отменить</Button>
      </div>
      {error ? <p role="alert">{error}</p> : null}
    </aside>
  )
}
