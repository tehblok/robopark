import { afterEach, describe, expect, it } from 'vitest'
import { ApiError, ApiTimeoutError } from '../../api'
import { classifyApiError } from './classifyApiError'

describe('classifyApiError', () => {
  afterEach(() => {
    Object.defineProperty(navigator, 'onLine', { configurable: true, value: true })
  })

  it.each([
    [401, null, 'unauthorized', 'Сессия истекла', false],
    [403, null, 'forbidden', 'Нет доступа', false],
    [404, null, 'not-found', 'Не найдено', false],
    [409, null, 'conflict', 'Данные изменились', true],
    [503, 'tracker_token_not_configured', 'configuration', 'Требуется настройка', false],
    [403, 'emergency_cookie_invalid', 'configuration', 'Требуется настройка', false],
    [503, null, 'server', 'Сервис временно недоступен', true],
    [503, 'temporary_upstream_not_configured', 'server', 'Сервис временно недоступен', true],
    [502, null, 'server', 'Сервис временно недоступен', true],
    [504, null, 'server', 'Сервис временно недоступен', true],
    [422, null, 'unknown', 'Не удалось выполнить действие', false],
  ] as const)('maps HTTP %s to %s', (status, detail, kind, title, isRetryable) => {
    const result = classifyApiError(new ApiError(status, detail, 'req-7'), 'Не удалось загрузить')
    expect(result).toMatchObject({
      kind,
      title,
      retryable: isRetryable,
      requestId: 'req-7',
    })
    expect(result.description.length).toBeGreaterThan(0)
  })

  it('recognizes a known not-configured detail regardless of non-5xx status', () => {
    expect(classifyApiError(new ApiError(409, 'emergency_cookie_not_configured'), 'Нужна настройка')).toMatchObject({
      kind: 'configuration',
      title: 'Требуется настройка',
      retryable: false,
    })
  })

  it('uses product-safe copy for an invalid robot-check integration', () => {
    expect(classifyApiError(new ApiError(403, 'emergency_cookie_invalid'), 'Нужна настройка')).toMatchObject({
      kind: 'configuration',
      description: 'Интеграция проверки робота требует внимания.',
    })
  })

  it.each([
    [new ApiError(401), 'unauthorized'],
    [new ApiError(403), 'forbidden'],
    [new ApiError(403, 'emergency_cookie_invalid'), 'configuration'],
  ] as const)('keeps HTTP authorization semantics while the browser is offline', (error, kind) => {
    Object.defineProperty(navigator, 'onLine', { configurable: true, value: false })

    expect(classifyApiError(error, 'Не удалось загрузить')).toMatchObject({ kind })
  })

  it('classifies a fetch TypeError as an offline failure', () => {
    expect(classifyApiError(new TypeError('Failed to fetch'), 'Не удалось загрузить')).toEqual({
      kind: 'offline',
      title: 'Нет сети',
      description: 'Проверьте подключение и повторите действие.',
      retryable: true,
    })
  })

  it('uses browser connectivity for otherwise unknown failures', () => {
    Object.defineProperty(navigator, 'onLine', { configurable: true, value: false })

    expect(classifyApiError(new Error('connection lost'), 'Не удалось загрузить')).toMatchObject({
      kind: 'offline',
      retryable: true,
    })
  })

  it('keeps a locally enforced timeout distinct from offline and server errors', () => {
    Object.defineProperty(navigator, 'onLine', { configurable: true, value: false })

    expect(classifyApiError(new ApiTimeoutError(30_000), 'Не удалось загрузить')).toEqual({
      kind: 'timeout',
      title: 'Сервис не ответил вовремя',
      description: 'Запрос отменён через 30 секунд. Повторите действие.',
      retryable: true,
    })
  })
})
