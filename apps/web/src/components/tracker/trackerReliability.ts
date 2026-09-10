import { ApiError, type TrackerIssueDetail } from '../../api'

type IssueState = Pick<TrackerIssueDetail, 'key' | 'status' | 'status_key' | 'assignee'>
const inFlight = new Map<string, Promise<unknown>>()

/** Prevent duplicate clicks while allowing a deliberate retry after any completed request. */
export async function runTrackerSubmission<T>(
  owner: string, issue: IssueState, action: string, payload: unknown,
  send: (headers: Record<string, string>) => Promise<T>,
  assertCurrent: () => void = () => undefined,
): Promise<T> {
  const identity = JSON.stringify([owner, issue.key, action, payload])
  const ongoing = inFlight.get(identity)
  if (ongoing) return ongoing as Promise<T>
  const perform = async () => {
    await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(payload)))
    assertCurrent()
    return send({
        'Idempotency-Key': crypto.randomUUID(),
        'X-Tracker-State': encodeURIComponent(JSON.stringify({
          status: issue.status, status_key: issue.status_key || '', assignee: issue.assignee?.login || '',
        })),
      })
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
  void owner; void issueKey
  return 0
}
