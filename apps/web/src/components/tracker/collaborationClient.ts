import { request } from '../../api'

export type Handoff = { revision: number; done: string; remaining: string; obstacles: string; author: string | null; updated_at: string | null }
export const collaborationClient = {
  presence: (key: string) => request<{ people: { username: string }[] }>(`/tracker/issues/${encodeURIComponent(key)}/presence`, { method: 'POST' }),
  handoff: (key: string) => request<Handoff>(`/tracker/issues/${encodeURIComponent(key)}/handoff`),
  save: (key: string, value: Handoff) => request<Handoff>(`/tracker/issues/${encodeURIComponent(key)}/handoff`, { method: 'PUT', body: JSON.stringify(value) }),
}
