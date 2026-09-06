import { ApiError, type TrackerIssueDetail } from '../../api'

type IssueState = Pick<TrackerIssueDetail, 'key' | 'status' | 'status_key' | 'assignee'>
const PREFIX = 'robopark:tracker-submission:v1:'
const inFlight = new Map<string, Promise<unknown>>()

/** Stable across reloads, with no silent fallback when durable storage is unavailable. */
export async function runTrackerSubmission<T>(
  owner: string, issue: IssueState, action: string, payload: unknown,
  send: (headers: Record<string, string>) => Promise<T>,
  assertCurrent: () => void = () => undefined,
): Promise<T> {
  const identity = JSON.stringify([owner, issue.key, action, payload])
  const ongoing = inFlight.get(identity)
  if (ongoing) return ongoing as Promise<T>
  const perform = async () => {
    let key: string
    let storageKey: string
    try {
      const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(payload)))
      const hash = [...new Uint8Array(digest)].map(x => x.toString(16).padStart(2, '0')).join('')
      assertCurrent()
      storageKey = PREFIX + JSON.stringify([owner, issue.key, action, hash])
      key = localStorage.getItem(storageKey) || crypto.randomUUID()
      localStorage.setItem(storageKey, key)
      window.dispatchEvent(new Event('tracker-submissions-changed'))
    } catch {
      throw new Error('Не удалось сохранить ключ отправки. Разрешите локальное хранилище и повторите.')
    }
    try {
      const result = await send({
        'Idempotency-Key': key,
        'X-Tracker-State': encodeURIComponent(JSON.stringify({
          status: issue.status, status_key: issue.status_key || '', assignee: issue.assignee?.login || '',
        })),
      })
      localStorage.removeItem(storageKey)
      window.dispatchEvent(new Event('tracker-submissions-changed'))
      return result
    } catch (error) {
      // Only state_conflict proves that this key has no reservation. Auth/busy
      // can precede lookup of an older uncertain entry, so retain those keys.
      if (error instanceof ApiError && error.detail === 'tracker_state_conflict') {
        localStorage.removeItem(storageKey)
        window.dispatchEvent(new Event('tracker-submissions-changed'))
      }
      throw error
    }
  }
  const promise = perform()
  inFlight.set(identity, promise)
  try { return await promise } finally { inFlight.delete(identity) }
}

export async function attachmentIdentity(file: File) {
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer())
  return { name: file.name, type: file.type, digest: [...new Uint8Array(digest)].map(x => x.toString(16).padStart(2, '0')).join('') }
}

export function trackerReliabilityError(error: unknown): string | null {
  if (error instanceof ApiError) {
    if (error.detail === 'tracker_task_busy') return 'Коллега сейчас отправляет изменение этой задачи. Подождите и проверьте обновлённую задачу. Черновик сохранён.'
    if (error.detail === 'tracker_state_conflict') return 'Статус или исполнитель изменились. Проверьте обновлённую задачу перед повторной отправкой. Черновик сохранён.'
    if (error.detail === 'tracker_submission_uncertain') return 'Результат отправки пока неизвестен. Проверьте историю в Tracker: повторная запись заблокирована, чтобы избежать дубля. Черновик сохранён.'
    if (error.detail === 'tracker_submission_payload_conflict') return 'Содержимое отправки изменилось. Обновите задачу и проверьте историю перед новой отправкой.'
  }
  if (error instanceof Error && error.message.startsWith('Не удалось сохранить ключ отправки')) return error.message
  return null
}

export function pendingTrackerSubmissions(owner: string | undefined, issueKey: string | undefined): number {
  if (!owner || !issueKey) return 0
  let count = 0
  try {
    for (let index = 0; index < localStorage.length; index++) {
      const key = localStorage.key(index)
      if (!key?.startsWith(PREFIX)) continue
      const identity = JSON.parse(key.slice(PREFIX.length))
      if (Array.isArray(identity) && identity[0] === owner && identity[1] === issueKey) ++count
    }
  } catch { /* Storage errors are reported before attempting any submission. */ }
  return count
}
