import { transformOnMac } from './computeImage'

const ACCEPTED_IMAGE_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp'])
const preparedFiles = new WeakSet<File>()

export type PreparedImage = {
  blob: Blob
  originalBlob?: Blob
  mimeType: string
  sizeBytes: number
  sha256: string
  previewUrl: string
  releasePreview(): void
}

export type PrepareImageOptions = {
  kind?: 'photo' | 'qr' | 'document'
  maxSourceBytes?: number
  maxEdge?: number
  quality?: number
}

function usefulPhoto(result: Blob | null, source: File): result is Blob {
  return result !== null && result.size > 0 && result.size < source.size && ACCEPTED_IMAGE_TYPES.has(result.type)
}

async function transformPhoto(source: File, maxEdge: number, quality: number): Promise<Blob> {
  const connection = (navigator as Navigator & { connection?: { saveData?: boolean, effectiveType?: string } }).connection
  const constrained = navigator.hardwareConcurrency > 0 && navigator.hardwareConcurrency <= 2
  // CPU-poor devices may ask a free Mac directly. A metered/slow source keeps
  // processing local to avoid transmitting its larger original unnecessarily.
  const peerFirst = constrained && source.size >= 1024 * 1024 && !connection?.saveData
    && !['slow-2g', '2g'].includes(connection?.effectiveType ?? '')
  if (peerFirst) {
    const peer = await transformOnMac(source, maxEdge, quality, 'direct')
    if (usefulPhoto(peer, source)) return peer
  }
  const local = await transformOffThread(source, maxEdge, quality).catch(() => source)
  if (usefulPhoto(local, source)) return local
  if (source.size >= 256 * 1024) {
    const peer = await transformOnMac(source, maxEdge, quality, peerFirst ? 'relay' : 'auto')
    if (usefulPhoto(peer, source)) return peer
  }
  return source
}

async function sha256(blob: Blob): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer())
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')
}

async function transformInBrowser(source: File, maxEdge: number, quality: number): Promise<Blob> {
  if (typeof createImageBitmap !== 'function' || typeof OffscreenCanvas === 'undefined') return source
  const bitmap = await createImageBitmap(source, { imageOrientation: 'from-image' })
  try {
    const scale = Math.min(1, maxEdge / Math.max(bitmap.width, bitmap.height))
    const width = Math.max(1, Math.round(bitmap.width * scale))
    const height = Math.max(1, Math.round(bitmap.height * scale))
    const canvas = new OffscreenCanvas(width, height)
    const context = canvas.getContext('2d')
    if (!context) return source
    context.drawImage(bitmap, 0, 0, width, height)
    return await canvas.convertToBlob({ type: 'image/webp', quality })
  } finally {
    bitmap.close()
  }
}

async function transformOffThread(source: File, maxEdge: number, quality: number): Promise<Blob> {
  if (typeof Worker === 'undefined' || typeof createImageBitmap !== 'function' || typeof OffscreenCanvas === 'undefined') {
    return transformInBrowser(source, maxEdge, quality)
  }
  const worker = new Worker(new URL('./media.worker.ts', import.meta.url), { type: 'module' })
  const id = crypto.randomUUID()
  let deadline: ReturnType<typeof setTimeout> | undefined
  try {
    return await new Promise<Blob>((resolve, reject) => {
      deadline = setTimeout(() => reject(new Error('media_processing_timeout')), 10000)
      worker.onmessage = (event: MessageEvent<{ id: string, blob?: Blob, error?: string }>) => {
        if (event.data.id !== id) return
        if (event.data.blob) resolve(event.data.blob)
        else reject(new Error(event.data.error || 'media_processing_failed'))
      }
      worker.onerror = () => reject(new Error('media_processing_failed'))
      worker.postMessage({ id, file: source, maxEdge, quality })
    })
  } finally {
    clearTimeout(deadline)
    worker.terminate()
  }
}

export async function prepareImage(source: File, options: PrepareImageOptions = {}): Promise<PreparedImage> {
  const kind = options.kind ?? 'photo'
  if (kind === 'document' ? !ACCEPTED_IMAGE_TYPES.has(source.type) && source.type !== 'application/pdf' : !ACCEPTED_IMAGE_TYPES.has(source.type)) throw new Error('media_invalid_type')
  if (source.size <= 0) throw new Error('media_empty')
  if (source.size > (options.maxSourceBytes ?? 15 * 1024 * 1024)) throw new Error('media_too_large')
  const converted = kind === 'photo'
    ? await transformPhoto(source, options.maxEdge ?? 1920, options.quality ?? 0.82)
    : source
  // A local optimization must never enlarge uploads or discard a usable original.
  const blob = converted.size > 0 && converted.size < source.size && ACCEPTED_IMAGE_TYPES.has(converted.type)
    ? converted : source
  const checksum = await sha256(blob)
  const previewUrl = URL.createObjectURL(blob)
  let released = false
  return {
    blob,
    originalBlob: blob === source ? undefined : source,
    mimeType: blob.type || source.type,
    sizeBytes: blob.size,
    sha256: checksum,
    previewUrl,
    releasePreview() {
      if (released) return
      released = true
      URL.revokeObjectURL(previewUrl)
    },
  }
}

/** Existing multipart photo flows share source-side compression and safe fallback. */
export async function preparePhotoFile(source: File): Promise<File> {
  if (preparedFiles.has(source) || !ACCEPTED_IMAGE_TYPES.has(source.type) || source.size < 256 * 1024) return source
  try {
    const prepared = await prepareImage(source)
    try {
      if (prepared.blob === source) { preparedFiles.add(source); return source }
      const extension = prepared.mimeType === 'image/webp' ? 'webp' : prepared.mimeType === 'image/png' ? 'png' : 'jpg'
      const result = new File([prepared.blob], `${source.name.replace(/\.[^.]+$/, '')}.${extension}`, { type: prepared.mimeType, lastModified: source.lastModified })
      preparedFiles.add(result)
      return result
    } finally { prepared.releasePreview() }
  } catch { return source }
}
