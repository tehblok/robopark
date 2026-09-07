import { ApiError, type User } from '../../api'

/** Keep privileged responses in memory and separate every authorization scope. */
export function adminResourceKey(kind: string, user: User | null) {
  return `admin:${kind}:${JSON.stringify([user?.id, user?.username, user?.role, user?.access_status, user?.permissions, user?.parks])}`
}

export const adminResourceOptions = {
  persist: false,
  refreshIntervalMs: 120_000,
  staleTimeMs: 120_000,
} as const


export function adminAccessFailure(...failures: unknown[]): ApiError | undefined {
  return failures.find((failure): failure is ApiError => failure instanceof ApiError && [401, 403].includes(failure.status))
}

export const adminAccessDeniedMessage = 'Доступ к управлению закрыт. Войдите заново или обратитесь к администратору.'
