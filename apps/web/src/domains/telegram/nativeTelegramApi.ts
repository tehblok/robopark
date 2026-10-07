import { request } from '../../api'

export type BotJobKind = 'report' | 'text' | 'zoom' | 'campaign'
export type BotJobSchedule = 'daily' | 'hourly' | 'once'
export type BotJobAlternate = 'all' | 'odd' | 'even'

export type ParkBot = {
  id: number
  name: string
  tag: string
  timezone: string
  chat_id: number | null
  thread_id: number | null
  revision: number
}

export type BotJobDraft = {
  park_id: number
  kind: BotJobKind
  title: string
  enabled: boolean
  schedule: BotJobSchedule
  time: string | null
  weekdays: number[]
  start_hour: number | null
  end_hour: number | null
  text: string | null
  url: string | null
  tracker_tag: string | null
  alternate: BotJobAlternate
  anchor_date: string | null
  timezone?: string | null
  run_at?: string | null
}

export type BotJob = BotJobDraft & { id: string; revision: number }

export type BotDelivery = {
  id: number
  job_id: string
  park_id: number
  title: string
  state: 'sent' | 'failed' | 'unknown' | string
  scheduled_at: string
  finished_at: string | null
  error_code: string | null
}

export type NativeBotHealth = {
  state: 'ready' | 'degraded' | 'offline' | 'unknown'
  telegram_ok: boolean | null
  scheduler_ok: boolean | null
  updated_at: string | null
  last_error: string | null
}

export type NativeTelegramState = {
  parks: ParkBot[]
  jobs: BotJob[]
  deliveries: BotDelivery[]
  health: NativeBotHealth
}

export type TelegramAccount = { linked: boolean; telegram_user_id: number | null }
export type TelegramLinkCode = { code: string; expires_at: string }

export type TelegramMigrationParkUpdate = {
  park_id: number
  park_tag: string
  location_key: string
  chat_id: number
  thread_id: number | null
}

export type TelegramMigrationJob = {
  source_ref: string
  source: 'schedules' | 'broadcasts' | 'campaigns'
  source_id: string
  park_id: number
  park_tag: string
  title: string
  kind: BotJobKind
  schedule: BotJobSchedule
  time: string | null
  start_hour: number | null
  end_hour: number | null
  weekdays: number[]
  text: string | null
  url: string | null
  tracker_tag: string | null
  alternate: BotJobAlternate
  anchor_date: string | null
}

export type TelegramMigrationConflict = {
  source: 'locations' | 'schedules' | 'broadcasts' | 'campaigns'
  source_id: string | null
  park_tag: string | null
  reason: 'park_not_found' | 'destination_conflict' | 'location_not_found' | 'unsupported_once' | 'unsupported_shape' | 'already_imported'
}

export type TelegramMigrationPreview = {
  fingerprint: string
  already_applied: boolean
  park_updates: TelegramMigrationParkUpdate[]
  jobs: TelegramMigrationJob[]
  conflicts: TelegramMigrationConflict[]
  counts: { park_updates: number; jobs: number; conflicts: number; skipped: number }
}

export type TelegramMigrationResult = TelegramMigrationPreview & { applied: true; applied_at: string }

export type NativeTelegramClient = {
  getAdmin(): Promise<NativeTelegramState>
  updatePark(id: number, body: Pick<ParkBot, 'chat_id' | 'thread_id' | 'revision'>): Promise<ParkBot>
  createJob(body: BotJobDraft): Promise<BotJob>
  updateJob(id: string, body: BotJobDraft & { revision: number }): Promise<BotJob>
  deleteJob(id: string, revision: number): Promise<void>
  runJob(id: string, body: { revision: number; request_id: string; allow_disabled: boolean }): Promise<{ delivery: BotDelivery; created: boolean }>
  getAccount(): Promise<TelegramAccount>
  createLinkCode(): Promise<TelegramLinkCode>
  unlinkAccount(): Promise<void>
  getMigrationPreview(): Promise<TelegramMigrationPreview>
  applyMigration(fingerprint: string): Promise<TelegramMigrationResult>
}

export const nativeTelegramClient: NativeTelegramClient = {
  getAdmin: () => request('/admin/bot/native'),
  updatePark: (id, body) => request(`/admin/bot/native/parks/${id}`, {
    method: 'PUT', body: JSON.stringify(body),
  }),
  createJob: body => request('/admin/bot/native/jobs', {
    method: 'POST', body: JSON.stringify(body),
  }),
  updateJob: (id, body) => request(`/admin/bot/native/jobs/${encodeURIComponent(id)}`, {
    method: 'PUT', body: JSON.stringify(body),
  }),
  deleteJob: (id, revision) => request(`/admin/bot/native/jobs/${encodeURIComponent(id)}?revision=${revision}`, {
    method: 'DELETE',
  }),
  runJob: (id, body) => request(`/admin/bot/native/jobs/${encodeURIComponent(id)}/run`, {
    method: 'POST', body: JSON.stringify(body),
  }),
  getAccount: () => request('/bot/account'),
  createLinkCode: () => request('/bot/account/link-code', { method: 'POST' }),
  unlinkAccount: () => request('/bot/account', { method: 'DELETE' }),
  getMigrationPreview: () => request('/admin/bot/native/migration/preview'),
  applyMigration: fingerprint => request('/admin/bot/native/migration/apply', {
    method: 'POST', body: JSON.stringify({ fingerprint }),
  }),
}
