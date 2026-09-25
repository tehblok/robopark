export type OtaManifestPreview = {
  format_version: number
  app_version: string
  compatible_from: string[]
  required_free_bytes: number
  changes: string[]
}

export type OtaHashProgress = { loaded: number; total: number }

export const OTA_MAX_BYTES = 2 * 1024 * 1024 * 1024
export const OTA_CHUNK_BYTES = 4 * 1024 * 1024
