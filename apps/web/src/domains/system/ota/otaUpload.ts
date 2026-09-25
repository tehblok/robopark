import { ApiError } from '../../../api'
import type { OtaUpload, OtaUploadClient } from '../../../opsApi'
import { OTA_CHUNK_BYTES, type OtaHashProgress } from './otaTypes'

export function hashOtaFile(file: File, onProgress: (value: OtaHashProgress) => void): Promise<string> {
  if (typeof Worker === 'undefined') return import('./otaHash.worker').then(({ hashFile }) => hashFile(file, (loaded, total) => onProgress({ loaded, total })))
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL('./otaHash.worker.ts', import.meta.url), { type: 'module' })
    worker.onmessage = (event: MessageEvent<{ type: string; loaded?: number; total?: number; sha256?: string; error?: string }>) => {
      if (event.data.type === 'progress') onProgress({ loaded: event.data.loaded ?? 0, total: event.data.total ?? file.size })
      if (event.data.type === 'result') { worker.terminate(); resolve(event.data.sha256 ?? '') }
      if (event.data.type === 'error') { worker.terminate(); reject(new Error(event.data.error ?? 'ota_hash_failed')) }
    }
    worker.onerror = () => { worker.terminate(); reject(new Error('ota_hash_failed')) }
    worker.postMessage({ file })
  })
}

const delay = (milliseconds: number, signal?: AbortSignal) => new Promise<void>((resolve, reject) => {
  const timer = window.setTimeout(resolve, milliseconds)
  signal?.addEventListener('abort', () => { window.clearTimeout(timer); reject(signal.reason) }, { once: true })
})

export async function uploadOtaFile(file: File, sha256: string, client: OtaUploadClient, onProgress: (offset: number) => void, signal?: AbortSignal): Promise<OtaUpload> {
  const upload = await client.create({ filename: file.name, size: file.size, sha256 })
  if (upload.state === 'verified' || upload.already_present) { onProgress(file.size); return upload }
  let offset = await client.offset(upload.upload_id)
  onProgress(offset)
  while (offset < file.size) {
    signal?.throwIfAborted()
    const chunk = file.slice(offset, Math.min(file.size, offset + (upload.chunk_size || OTA_CHUNK_BYTES)))
    let lastError: unknown
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        offset = await client.append(upload.upload_id, offset, chunk)
        onProgress(offset)
        lastError = null
        break
      } catch (caught) {
        lastError = caught
        if (caught instanceof ApiError && caught.status === 409) {
          offset = await client.offset(upload.upload_id)
          if (offset >= file.size) break
        }
        if (attempt < 2) await delay(250 * (2 ** attempt) + Math.floor(Math.random() * 100), signal)
      }
    }
    if (lastError) throw lastError
  }
  const finalized = await client.finalize(upload.upload_id)
  if (finalized.sha256 !== sha256 || finalized.size !== file.size || finalized.state !== 'verified') throw new Error('ota_server_verification_failed')
  return finalized
}
