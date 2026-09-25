import { sha256 } from '@noble/hashes/sha2.js'
import { bytesToHex } from '@noble/hashes/utils.js'

type HashRequest = { file: File }
type HashResponse = { type: 'progress'; loaded: number; total: number } | { type: 'result'; sha256: string } | { type: 'error'; error: string }

export async function hashFile(file: Blob, onProgress: (loaded: number, total: number) => void = () => {}): Promise<string> {
  const digest = sha256.create()
  const total = file.size
  let loaded = 0
  const read = (blob: Blob): Promise<ArrayBuffer> => typeof blob.arrayBuffer === 'function' ? blob.arrayBuffer() : new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as ArrayBuffer)
    reader.onerror = () => reject(reader.error)
    reader.readAsArrayBuffer(blob)
  })
  for (let offset = 0; offset < total; offset += 4 * 1024 * 1024) {
    const value = new Uint8Array(await read(file.slice(offset, Math.min(total, offset + 4 * 1024 * 1024))))
    digest.update(value)
    loaded += value.byteLength
    onProgress(loaded, total)
  }
  return bytesToHex(digest.digest())
}

const workerScope = typeof self !== 'undefined' ? self as unknown as Worker : null
if (workerScope && typeof document === 'undefined') {
  workerScope.onmessage = async (event: MessageEvent<HashRequest>) => {
    try {
      const sha = await hashFile(event.data.file, (loaded, total) => workerScope.postMessage({ type: 'progress', loaded, total } satisfies HashResponse))
      workerScope.postMessage({ type: 'result', sha256: sha } satisfies HashResponse)
    } catch (caught) {
      workerScope.postMessage({ type: 'error', error: caught instanceof Error ? caught.message : 'ota_hash_failed' } satisfies HashResponse)
    }
  }
}
