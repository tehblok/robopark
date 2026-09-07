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

it.each([
  ['confirm_required', 400, 'Введите фразу подтверждения точно, без лишних пробелов.'],
  ['github_release_unavailable', 400, 'Релиз больше недоступен. Список обновлений проверяется заново.'],
  ['github_approval_actor_mismatch', 400, 'Этот релиз уже подтверждён другим владельцем. Дождитесь завершения операции.'],
  ['host_work_in_progress', 409, 'Другая операция уже выполняется.'],
  ['job_in_progress', 409, 'Другая операция уже выполняется.'],
  ['host_operation_failed', 400, 'Хост не смог завершить операцию. Соберите диагностику для проверки.'],
])('maps host operation error %s without leaking implementation details', (detail, status, expected) => {
  expect(mapApiError(new ApiError(status, detail), 'Ошибка')).toBe(expected)
})

it('explains bounded archive storage admission', () => {
  expect(mapApiError(new ApiError(409, 'artifact_storage_full'))).toBe('Недостаточно места для нового архива. Дождитесь очистки или проверьте диагностику хоста.')
})
