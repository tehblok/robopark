import { afterEach, describe, expect, it } from 'vitest'
import {
  clearOperationReservation,
  operationReservationKey,
  readOperationReservation,
  writeOperationReservation,
} from './operationReservation'

const alice = { id: 1, username: 'alice' }
const bob = { id: 2, username: 'bob' }
const reservation = {
  id: '11111111-1111-4111-8111-111111111111',
  kind: 'diagnostics' as const,
  created_at: 1,
  phase: 'reconciling' as const,
}

afterEach(() => localStorage.clear())

describe('operation reservation ownership', () => {
  it('never exposes one royal operation UUID to another account in the same browser', () => {
    writeOperationReservation(alice, reservation)

    expect(readOperationReservation(alice)).toEqual(reservation)
    expect(readOperationReservation(bob)).toBeNull()
    expect(localStorage.getItem(operationReservationKey(alice))).not.toBeNull()
    expect(localStorage.getItem(operationReservationKey(bob))).toBeNull()
  })

  it('uses the immutable account id when a username changes', () => {
    writeOperationReservation(alice, reservation)

    expect(readOperationReservation({ ...alice, username: 'alice-renamed' })).toEqual(reservation)
  })

  it('clears only the selected actor reservation', () => {
    writeOperationReservation(alice, reservation)
    writeOperationReservation(bob, { ...reservation, id: '22222222-2222-4222-8222-222222222222' })

    clearOperationReservation(alice)

    expect(readOperationReservation(alice)).toBeNull()
    expect(readOperationReservation(bob)?.id).toBe('22222222-2222-4222-8222-222222222222')
  })
})
