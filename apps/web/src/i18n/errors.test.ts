import { describe, expect, it } from 'vitest'
import { ApiError } from '../api'
import { mapApiError, mapLoginError } from './errors'
import { ru } from './ru'

describe('mapApiError', () => {
  it('never presents the technical Emergency product name in robot-check failures', () => {
    for (const detail of ['emergency_upstream_error', 'emergency_cookie_not_configured', 'emergency_cookie_invalid', 'unmapped']) {
      expect(mapApiError(new ApiError(503, detail), ru.errors.emergency)).not.toMatch(/\bEmergency\b/i)
    }
    expect(ru.errors.emergency503).not.toMatch(/\bEmergency\b/i)
  })
  it('uses product-safe robot-check copy for integration configuration errors', () => {
    const expected = 'Интеграция проверки робота требует внимания.'
    expect(mapApiError(new ApiError(403, 'emergency_cookie_invalid'), 'fb')).toBe(expected)
    expect(mapApiError(new ApiError(503, 'emergency_cookie_not_configured'), 'fb')).toBe(expected)

    const session = new ApiError(401, 'session_expired')
    expect(mapApiError(session, 'fb')).toBe(ru.errors.sessionExpired)
    const bare = new ApiError(401, '')
    expect(mapApiError(bare, 'fb')).toBe(ru.errors.sessionExpired)
  })

  it('maps too_many_attempts', () => {
    const err = new ApiError(429, 'too_many_attempts')
    expect(mapApiError(err, ru.errors.login)).toBe(ru.errors.details.too_many_attempts)
  })
})

describe('mapLoginError', () => {
  it('maps bare 401 to credential copy, not sessionExpired', () => {
    // ChangePassword uses this mapper so a failed current password is not «Сессия истекла».
    expect(mapLoginError(new ApiError(401))).toBe(ru.errors.login)
    expect(mapLoginError(new ApiError(401, null))).toBe(ru.errors.login)
  })

  it('maps too_many_attempts via mapApiError', () => {
    expect(mapLoginError(new ApiError(429, 'too_many_attempts'))).toBe(
      ru.errors.details.too_many_attempts,
    )
  })
})
