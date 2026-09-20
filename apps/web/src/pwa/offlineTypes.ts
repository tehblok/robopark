export type OfflineScope = {
  account: string
  role: string
  permissions: string
  park: string
  schema: number
}

export type OfflineActionState =
  | 'local'
  | 'ready'
  | 'sending'
  | 'confirmed'
  | 'conflict'
  | 'attention'
  | 'cancelled'

export type OfflineAction = {
  id: string
  deviceId: string
  resourceType: string
  resourceId: string
  action: string
  idempotencyKey: string
  baseRevision: string | null
  dependencies: string[]
  payload: unknown
  state: OfflineActionState
  attempts: number
  createdAt: number
  updatedAt: number
}

export type OfflineMediaState = 'local' | 'ready' | 'uploading' | 'confirmed' | 'attention'

export type OfflineMedia = {
  id: string
  actionId: string
  issueKey: string
  name: string
  blob: Blob
  mimeType: string
  sha256: string
  sizeBytes: number
  state: OfflineMediaState
  createdAt: number
  updatedAt: number
}

export type OfflineCleanupOptions = {
  maxBytes: number
  now?: number
  confirmedTtlMs?: number
  entityTtlMs?: number
}
