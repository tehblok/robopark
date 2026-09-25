import type { HostOperationKind } from '../../opsApi'

export const OPERATION_KEY = 'robopark:system-operation'
export const NOT_FOUND_GRACE_MS = 15_000

export type OperationReservation = {
  id: string
  kind: HostOperationKind | ''
  created_at: number
  phase: 'posting' | 'reconciling'
}

export function readOperationReservation(): OperationReservation | null {
  const raw = localStorage.getItem(OPERATION_KEY)
  if (!raw) return null
  try {
    const value = JSON.parse(raw) as Partial<OperationReservation>
    if (typeof value.id !== 'string' || !value.id) return null
    return {
      id: value.id,
      kind: typeof value.kind === 'string' ? value.kind as HostOperationKind : '',
      created_at: typeof value.created_at === 'number' ? value.created_at : 0,
      phase: value.phase === 'posting' ? 'posting' : 'reconciling',
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
