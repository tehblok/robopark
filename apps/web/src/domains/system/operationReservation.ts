import type { HostOperationKind, HostOperationPayload } from '../../opsApi'

export const OPERATION_KEY_PREFIX = 'robopark:system-operation:'
export const LEGACY_OPERATION_KEY = 'robopark:system-operation'
export type OperationActor = { id: number, username: string }
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
  'plan_id', 'upload_id', 'sha256', 'version',
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

export function operationReservationKey(actor: OperationActor): string {
  return `${OPERATION_KEY_PREFIX}${actor.id}`
}

export function readOperationReservation(actor: OperationActor): OperationReservation | null {
  const raw = localStorage.getItem(operationReservationKey(actor))
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

export function writeOperationReservation(actor: OperationActor, value: OperationReservation) {
  localStorage.setItem(operationReservationKey(actor), JSON.stringify(value))
}

export function clearOperationReservation(actor: OperationActor) {
  localStorage.removeItem(operationReservationKey(actor))
}
