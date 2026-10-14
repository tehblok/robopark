import { requestHybridJob, type ComputeRoute } from './computeHybrid'

export function bytesBase64(bytes: Uint8Array): string {
  let text = ''
  for (let offset = 0; offset < bytes.length; offset += 8192) text += String.fromCharCode(...bytes.subarray(offset, offset + 8192))
  return btoa(text)
}

// Read dimensions before decoding so a bogus peer cannot trigger a huge allocation.
function jpegDimensions(bytes: Uint8Array): [number, number] | null {
  if (bytes.length < 4 || bytes[0] !== 255 || bytes[1] !== 216 || bytes.at(-2) !== 255 || bytes.at(-1) !== 217) return null
  let offset = 2
  while (offset + 4 <= bytes.length) {
    if (bytes[offset++] !== 255) return null
    while (bytes[offset] === 255) offset++
    const marker = bytes[offset++]
    if (marker === 218 || marker === 217) return null
    const length = bytes[offset] * 256 + bytes[offset + 1]
    if (length < 2 || offset + length > bytes.length) return null
    if ([192, 193, 194, 195, 197, 198, 199, 201, 202, 203].includes(marker)) {
      if (length < 8) return null
      return [bytes[offset + 5] * 256 + bytes[offset + 6], bytes[offset + 3] * 256 + bytes[offset + 4]]
    }
    offset += length
  }
  return null
}

async function decodedDimensions(blob: Blob): Promise<[number, number]> {
  let expired = false
  let image: HTMLImageElement | undefined
  let url: string | undefined
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    const decoding = typeof createImageBitmap === 'function'
      ? createImageBitmap(blob).then(bitmap => {
        const dimensions: [number, number] = [bitmap.width, bitmap.height]
        bitmap.close()
        if (expired) throw new Error('decode_expired')
        return dimensions
      })
      : (async (): Promise<[number, number]> => {
        image = new Image(); url = URL.createObjectURL(blob); image.src = url
        await image.decode()
        return [image.naturalWidth, image.naturalHeight]
      })()
    return await Promise.race([decoding, new Promise<never>((_, reject) => {
      timer = setTimeout(() => { expired = true; reject(new Error('decode_timeout')) }, 3000)
    })])
  } finally {
    clearTimeout(timer)
    if (image) image.src = ''
    if (url) URL.revokeObjectURL(url)
  }
}

export async function transformOnMac(source: File, maxEdge: number, quality: number, route: ComputeRoute = 'auto'): Promise<Blob | null> {
  if (navigator.onLine === false || source.size < 256 * 1024 || source.size > 4 * 1024 * 1024
    || !['image/jpeg', 'image/png', 'image/webp'].includes(source.type)) return null
  let output: Blob | null = null
  try {
    const reply = await requestHybridJob('image_resize_v1', {
      data_base64: bytesBase64(new Uint8Array(await source.arrayBuffer())),
      mime_type: source.type, max_edge: maxEdge, quality: Math.round(quality * 100),
    }, async candidate => {
      if (candidate.mime_type !== 'image/jpeg' || typeof candidate.data_base64 !== 'string'
        || candidate.data_base64.length > Math.ceil(4 * 1024 * 1024 / 3) * 4
        || typeof candidate.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(candidate.sha256)
        || !Number.isInteger(candidate.width) || !Number.isInteger(candidate.height)) return false
      const bytes = Uint8Array.from(atob(candidate.data_base64), value => value.charCodeAt(0))
      const size = jpegDimensions(bytes)
      if (bytes.length >= source.size || !size || Math.min(...size) < 1 || Math.max(...size) > maxEdge
        || size[0] !== candidate.width || size[1] !== candidate.height) return false
      const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), value => value.toString(16).padStart(2, '0')).join('')
      if (hash !== candidate.sha256) return false
      const blob = new Blob([bytes], { type: 'image/jpeg' })
      const decoded = await decodedDimensions(blob)
      if (decoded[0] !== size[0] || decoded[1] !== size[1]) return false
      output = blob
      return true
    }, route)
    return reply ? output : null
  } catch { return null }
}
