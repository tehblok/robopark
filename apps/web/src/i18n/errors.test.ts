import { describe, expect, it } from 'vitest'
import { ApiError } from '../api'
import { mapApiError, mapLoginError } from './errors'
import { ru } from './ru'

describe('mapApiError', () => {
  it('uses Emergency copy only for emergency_cookie_invalid', () => {
    const emergency = new ApiError(401, 'emergency_cookie_invalid')
    expect(mapApiError(emergency, 'fb')).toBe(ru.errors.details.emergency_cookie_invalid)
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
