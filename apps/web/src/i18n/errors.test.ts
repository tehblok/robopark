import { describe, expect, it } from 'vitest'
import { ApiError } from '../api'
import { mapApiError, mapLoginError } from './errors'
import { ru } from './ru'

describe('mapApiError', () => {
  it('does not describe an unrelated HTTP 503 as an unconfigured Tracker token', () => {
    expect(mapApiError(new ApiError(503), 'Не удалось загрузить историю процесса.')).toBe('Не удалось загрузить историю процесса.')
    expect(mapApiError(new ApiError(503, 'tracker_token_not_configured'), 'Не удалось загрузить историю процесса.')).toBe(
      ru.errors.details.tracker_token_not_configured,
    )
  })
  it('never presents the technical Emergency product name in robot-check failures', () => {
    for (const detail of ['emergency_upstream_error', 'emergency_cookie_not_configured', 'emergency_cookie_invalid', 'unmapped']) {
      expect(mapApiError(new ApiError(503, detail), ru.errors.emergency)).not.toMatch(/\bEmergency\b/i)
    }
    expect(ru.errors.emergency503).not.toMatch(/\bEmergency\b/i)
  })
  it('uses product-safe robot-check copy for integration configuration errors', () => {
    expect(mapApiError(new ApiError(403, 'emergency_cookie_invalid'), 'fb')).toBe(
      'Cookie проверки робота отклонена. Скопируйте свежую сессию и повторите.',
    )
    expect(mapApiError(new ApiError(503, 'emergency_cookie_not_configured'), 'fb')).toBe(
      'Cookie проверки робота не настроена.',
    )

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
  ['request_superseded', 400, 'Запрос обновления отменён после другой установки. Проверьте текущую версию и повторите при необходимости.'],
])('maps host operation error %s without leaking implementation details', (detail, status, expected) => {
  expect(mapApiError(new ApiError(status, detail), 'Ошибка')).toBe(expected)
})

it('explains bounded archive storage admission', () => {
  expect(mapApiError(new ApiError(409, 'artifact_storage_full'))).toBe('Недостаточно места для нового архива. Дождитесь очистки или проверьте диагностику хоста.')
})

it.each([
  ['tracker_attach_disabled', 403, 'Нет прав прикреплять фото к этой задаче.'],
  ['media_issue_not_found', 404, 'Задача не найдена. Обновите список задач.'],
  ['media_upload_user_quota_exceeded', 429, 'Достигнут лимит загрузок фото для аккаунта. Дождитесь завершения текущих загрузок или очистки.'],
  ['media_upload_global_quota_exceeded', 429, 'Хранилище загрузок достигло лимита. Обратитесь к владельцу для очистки.'],
  ['media_storage_capacity_exceeded', 507, 'Недостаточно свободного места для фото. Освободите хранилище и повторите.'],
  ['media_storage_unavailable', 503, 'Хранилище фото временно недоступно. Повторите позже.'],
  ['media_upload_scope_refresh_required', 409, 'Права на загрузку нужно проверить заново. Повторите синхронизацию.'],
])('explains media admission error %s', (detail, status, expected) => {
  expect(mapApiError(new ApiError(status, detail), 'Не удалось прикрепить фото.')).toBe(expected)
})

it.each([
  ['build_failed', 'Сборка новой версии не завершилась. Рабочая версия сохранена.'],
  ['tests_failed', 'Тесты пакета не прошли. Живая система не изменена.'],
  ['migration_failed', 'Не удалось обновить структуру базы. Выполнен откат.'],
  ['docker_disk_full', 'Для сборки не хватает места на диске. Освободите место и повторите обновление.'],
  ['docker_network_failed', 'Docker не смог скачать зависимости. Проверьте интернет и повторите обновление.'],
  ['docker_out_of_memory', 'Во время сборки закончилась память. Перезапустите хост и повторите обновление.'],
  ['frontend_typescript_failed', 'Веб-интерфейс не прошёл проверку TypeScript. Рабочая версия сохранена.'],
])('explains OTA failure %s', (detail, expected) => {
  expect(mapApiError(new ApiError(400, detail), 'Ошибка')).toBe(expected)
})
