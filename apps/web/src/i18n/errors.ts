import { ApiError } from '../api'
import { ru } from './ru'

export function mapApiError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.detail && ru.errors.details[error.detail]) {
      return ru.errors.details[error.detail]
    }
    if (error.status === 503) return ru.errors.tasks503
    if (error.status === 401) return ru.errors.emergency401
    if (error.status === 409) return ru.errors.tasks409
  }
  if (error instanceof Error) {
    if (error.message === '403') return ru.errors.register403
    if (error.message === '409') return ru.errors.register409
  }
  return fallback
}
