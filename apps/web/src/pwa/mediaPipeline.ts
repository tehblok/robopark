const ACCEPTED_IMAGE_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp'])

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
  try {
    return await new Promise<Blob>((resolve, reject) => {
      worker.onmessage = (event: MessageEvent<{ id: string, blob?: Blob, error?: string }>) => {
        if (event.data.id !== id) return
        if (event.data.blob) resolve(event.data.blob)
        else reject(new Error(event.data.error || 'media_processing_failed'))
      }
      worker.onerror = () => reject(new Error('media_processing_failed'))
      worker.postMessage({ id, file: source, maxEdge, quality })
    })
  } finally {
    worker.terminate()
  }
}

export async function prepareImage(source: File, options: PrepareImageOptions = {}): Promise<PreparedImage> {
  const kind = options.kind ?? 'photo'
  if (kind === 'document' ? !ACCEPTED_IMAGE_TYPES.has(source.type) && source.type !== 'application/pdf' : !ACCEPTED_IMAGE_TYPES.has(source.type)) throw new Error('media_invalid_type')
  if (source.size <= 0) throw new Error('media_empty')
  if (source.size > (options.maxSourceBytes ?? 15 * 1024 * 1024)) throw new Error('media_too_large')
  const blob = kind === 'photo'
    ? await transformOffThread(source, options.maxEdge ?? 1920, options.quality ?? 0.82).catch(() => source)
    : source
  const previewUrl = URL.createObjectURL(blob)
  let released = false
  return {
    blob,
    originalBlob: blob === source ? undefined : source,
    mimeType: blob.type || source.type,
    sizeBytes: blob.size,
    sha256: await sha256(blob),
    previewUrl,
    releasePreview() {
      if (released) return
      released = true
      URL.revokeObjectURL(previewUrl)
    },
  }
}
