import { ApiError } from '../api'
import { ru } from './ru'

export function mapApiError(error: unknown, fallback = ''): string {
  if (error instanceof ApiError) {
    if (error.detail && ru.errors.details[error.detail]) {
      return ru.errors.details[error.detail]
    }
    if (error.detail === 'maintenance' || (error.status === 503 && error.detail === 'maintenance')) {
      return ru.maintenance.title
    }
    if (error.status === 503) return ru.errors.tasks503
    if (error.status === 401) {
      if (error.detail === 'emergency_cookie_invalid') return ru.errors.emergency401
      return ru.errors.sessionExpired
    }
    if (error.status === 409) return ru.errors.tasks409
    if (error.status === 404) return ru.errors.notFound
    if (error.status === 502) return ru.errors.details.tracker_upstream_error
    // Never surface raw upstream/API detail strings in the UI.
  }
  if (error instanceof Error) {
    if (error.message === '403') return ru.errors.register403
    if (error.message === '409') return ru.errors.register409
  }
  return fallback
}
