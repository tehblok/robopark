import { ApiError, fetchWithTimeout } from '../../api'

export const CONFIG_SECTIONS = [
  'roles', 'users', 'locations', 'schedules', 'broadcasts', 'campaigns',
  'auxiliary_tracker_queues', 'profile', 'dispatcher_pause', 'send_pause',
] as const
export type BotConfigSection = typeof CONFIG_SECTIONS[number]
export type ConfigValue = { revision: string; value: unknown }
export type BotConfigDocument = { sections: Record<BotConfigSection, ConfigValue> }
export type BotConfigClient = {
  read(): Promise<BotConfigDocument>
  update(section: BotConfigSection, value: unknown, revision: string): Promise<ConfigValue>
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  return fetchWithTimeout(`/api/admin/bot/config${path}`, { credentials: 'include', ...init }, 15_000, async response => {
    if (!response.ok) {
      let detail: unknown = null
      try { detail = (await response.json()).detail ?? null } catch { /* bounded error */ }
      throw new ApiError(response.status, detail)
    }
    return await response.json() as T
  })
}

export const botConfigClient: BotConfigClient = {
  read: () => request(''),
  update: (section, value, revision) => request(`/${section}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', 'If-Match': `"${revision}"` },
    body: JSON.stringify({ value }),
  }),
}
