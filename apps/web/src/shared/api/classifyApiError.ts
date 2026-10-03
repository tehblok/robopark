import { ApiError, ApiTimeoutError } from '../../api'
import { mapApiError } from '../../i18n/errors'

export type DomainErrorKind =
  | 'offline'
  | 'timeout'
  | 'unauthorized'
  | 'forbidden'
  | 'not-found'
  | 'conflict'
  | 'configuration'
  | 'server'
  | 'unknown'

export type DomainError = {
  kind: DomainErrorKind
  title: string
  description: string
  retryable: boolean
  requestId?: string
}

type ApiDomainErrorKind = Exclude<DomainErrorKind, 'offline' | 'timeout'>

const titles: Record<ApiDomainErrorKind, string> = {
  unauthorized: 'Сессия истекла',
  forbidden: 'Нет доступа',
  'not-found': 'Не найдено',
  conflict: 'Данные изменились',
  configuration: 'Требуется настройка',
  server: 'Сервис временно недоступен',
  unknown: 'Не удалось выполнить действие',
}

const retryable: Record<ApiDomainErrorKind, boolean> = {
  unauthorized: false,
  forbidden: false,
  'not-found': false,
  conflict: true,
  configuration: false,
  server: true,
  unknown: false,
}

const configurationDetails = new Set([
  'tracker_token_not_configured',
  'blockers_disabled_for_park',
  'emergency_cookie_not_configured',
  'emergency_cookie_invalid',
])

function classifyHttpError(error: ApiError): ApiDomainErrorKind {
  if (error.status === 401) return 'unauthorized'
  if (configurationDetails.has(error.detail ?? '')) return 'configuration'
  if (error.status === 403) return 'forbidden'
  if (error.status === 404) return 'not-found'
  if (error.status === 409) return 'conflict'
  if (error.status === 429 || error.status >= 500) return 'server'
  return 'unknown'
}

export function classifyApiError(error: unknown, fallback: string): DomainError {
  if (error instanceof ApiTimeoutError) {
    return {
      kind: 'timeout',
      title: 'Сервис не ответил вовремя',
      description: `Запрос отменён через ${Math.round(error.timeoutMs / 1000)} секунд. Повторите действие.`,
      retryable: true,
    }
  }

  if (error instanceof ApiError) {
    const kind = classifyHttpError(error)
    const description = mapApiError(error, fallback) || fallback
    return {
      kind,
      title: titles[kind],
      description,
      retryable: retryable[kind],
      ...(error.requestId ? { requestId: error.requestId } : {}),
    }
  }

  if (error instanceof TypeError || (typeof navigator !== 'undefined' && !navigator.onLine)) {
    return {
      kind: 'offline',
      title: 'Нет сети',
      description: 'Проверьте подключение и повторите действие.',
      retryable: true,
    }
  }

  const kind: ApiDomainErrorKind = 'unknown'
  return {
    kind,
    title: titles[kind],
    description: mapApiError(error, fallback) || fallback,
    retryable: retryable[kind],
  }
}
