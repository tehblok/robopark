export type UploadableMedia = { id: string, actionId: string, deviceId: string, blob: Blob, mimeType: string, sha256: string, name: string }
export type UploadSession = {
  upload_id: string
  received_offset: number
  completed: boolean
  media_id?: string
  status: 'active' | 'reinitialized' | 'completed'
}
export type CompletedUpload = { upload_id: string, media_id: string, completed: true }
export type ResumableUploadApi = {
  create(input: { media_id: string, dependent_action_id: string, device_id: string, name: string, mime_type: string, size_bytes: number, sha256: string }, signal?: AbortSignal): Promise<UploadSession>
  putChunk(uploadId: string, offset: number, chunk: Blob, sha256: string, signal?: AbortSignal): Promise<{ received_offset: number }>
  complete(uploadId: string, signal?: AbortSignal): Promise<CompletedUpload>
}

function checkAborted(signal?: AbortSignal): void {
  if (signal?.aborted) throw signal.reason ?? new DOMException('Upload cancelled', 'AbortError')
}

async function chunkHash(chunk: Blob): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await chunk.arrayBuffer())
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')
}

export async function uploadMedia(
  media: UploadableMedia,
  api: ResumableUploadApi,
  options: { chunkBytes?: number, signal?: AbortSignal } = {},
): Promise<CompletedUpload> {
  checkAborted(options.signal)
  const input = {
    media_id: media.id,
    dependent_action_id: media.actionId,
    device_id: media.deviceId,
    name: media.name,
    mime_type: media.mimeType,
    size_bytes: media.blob.size,
    sha256: media.sha256,
  }
  const session = await (options.signal ? api.create(input, options.signal) : api.create(input))
  checkAborted(options.signal)
  if (session.status === 'completed' && session.completed && session.media_id) {
    return { upload_id: session.upload_id, media_id: session.media_id, completed: true }
  }
  let offset = session.received_offset
  if (offset < 0 || offset > media.blob.size) throw new Error('media_offset_invalid')
  const chunkBytes = Math.max(64 * 1024, options.chunkBytes ?? 512 * 1024)
  // Tests and very small files still use the requested exact chunk size.
  const boundedChunkBytes = options.chunkBytes != null ? Math.max(1, options.chunkBytes) : chunkBytes
  while (offset < media.blob.size) {
    checkAborted(options.signal)
    const chunk = media.blob.slice(offset, Math.min(media.blob.size, offset + boundedChunkBytes), media.mimeType)
    const hash = await chunkHash(chunk)
    checkAborted(options.signal)
    const response = await (options.signal
      ? api.putChunk(session.upload_id, offset, chunk, hash, options.signal)
      : api.putChunk(session.upload_id, offset, chunk, hash))
    checkAborted(options.signal)
    if (response.received_offset !== offset + chunk.size) throw new Error('media_offset_mismatch')
    offset = response.received_offset
  }
  checkAborted(options.signal)
  return options.signal ? api.complete(session.upload_id, options.signal) : api.complete(session.upload_id)
}
