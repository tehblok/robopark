import type { HostOperationKind, HostOperationPayload } from '../../opsApi'

export const OPERATION_KEY = 'robopark:system-operation'
export type SafeOperationDraft = {
  operation_id: string
  kind: HostOperationKind
  capability_revision: string
} & Record<string, unknown>

export type OperationReservation = {
  id: string
  kind: HostOperationKind | ''
  created_at: number
  phase: 'posting' | 'reconciling'
  draft?: SafeOperationDraft
}

const SAFE_DRAFT_FIELDS = new Set([
  'operation_id', 'kind', 'capability_revision', 'release', 'release_id',
  'package', 'service', 'services', 'categories', 'device_uuid', 'backup_id',
  'plan_id',
])

export function safeOperationDraft(payload: HostOperationPayload): SafeOperationDraft {
  return Object.fromEntries(
    Object.entries(payload).filter(([key]) => SAFE_DRAFT_FIELDS.has(key)),
  ) as SafeOperationDraft
}

function readSafeDraft(value: unknown, id: string, kind: string): SafeOperationDraft | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  const draft = Object.fromEntries(
    Object.entries(value).filter(([key]) => SAFE_DRAFT_FIELDS.has(key)),
  ) as Partial<SafeOperationDraft>
  if (
    draft.operation_id !== id || draft.kind !== kind
    || typeof draft.capability_revision !== 'string'
  ) return undefined
  return draft as SafeOperationDraft
}

export function readOperationReservation(): OperationReservation | null {
  const raw = localStorage.getItem(OPERATION_KEY)
  if (!raw) return null
  try {
    const value = JSON.parse(raw) as Partial<OperationReservation>
    if (typeof value.id !== 'string' || !value.id) return null
    const kind = typeof value.kind === 'string' ? value.kind as HostOperationKind : ''
    return {
      id: value.id,
      kind,
      created_at: typeof value.created_at === 'number' ? value.created_at : 0,
      phase: value.phase === 'posting' ? 'posting' : 'reconciling',
      draft: readSafeDraft(value.draft, value.id, kind),
    }
  } catch {
    return { id: raw, kind: '', created_at: 0, phase: 'reconciling' }
  }
}

export function writeOperationReservation(value: OperationReservation) {
  localStorage.setItem(OPERATION_KEY, JSON.stringify(value))
}

export function clearOperationReservation() {
  localStorage.removeItem(OPERATION_KEY)
}
