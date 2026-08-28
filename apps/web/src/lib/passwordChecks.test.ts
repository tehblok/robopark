import { describe, expect, it } from 'vitest'
import { passwordChecks } from './passwordChecks'

describe('passwordChecks', () => {
  it('rejects a short password even with mixed classes', () => {
    const result = passwordChecks('Ab1!')
    expect(result.length).toBe(false)
    expect(result.classes).toBe(4)
    expect(result.ok).toBe(false)
  })

  it('accepts 12+ chars with three classes', () => {
    const result = passwordChecks('RoboparkPass1')
    expect(result.length).toBe(true)
    expect(result.classes).toBe(3)
    expect(result.ok).toBe(true)
  })
})
