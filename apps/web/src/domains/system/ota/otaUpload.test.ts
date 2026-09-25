import { describe, expect, it, vi } from 'vitest'
import type { OtaUpload, OtaUploadClient } from '../../../opsApi'
import { hashFile } from './otaHash.worker'
import { inspectOtaFile } from './otaManifest'
import { uploadOtaFile } from './otaUpload'

function storedZip(name: string, body: Uint8Array): Uint8Array {
  const encoder = new TextEncoder()
  const filename = encoder.encode(name)
  const local = new Uint8Array(30 + filename.length + body.length)
  const localView = new DataView(local.buffer)
  localView.setUint32(0, 0x04034b50, true); localView.setUint16(4, 20, true)
  localView.setUint32(18, body.length, true); localView.setUint32(22, body.length, true)
  localView.setUint16(26, filename.length, true); local.set(filename, 30); local.set(body, 30 + filename.length)
  const central = new Uint8Array(46 + filename.length)
  const centralView = new DataView(central.buffer)
  centralView.setUint32(0, 0x02014b50, true); centralView.setUint16(4, 20, true); centralView.setUint16(6, 20, true)
  centralView.setUint32(20, body.length, true); centralView.setUint32(24, body.length, true)
  centralView.setUint16(28, filename.length, true); central.set(filename, 46)
  const eocd = new Uint8Array(22)
  const eocdView = new DataView(eocd.buffer)
  eocdView.setUint32(0, 0x06054b50, true); eocdView.setUint16(8, 1, true); eocdView.setUint16(10, 1, true)
  eocdView.setUint32(12, central.length, true); eocdView.setUint32(16, local.length, true)
  const result = new Uint8Array(local.length + central.length + eocd.length)
  result.set(local); result.set(central, local.length); result.set(eocd, local.length + central.length)
  return result
}

function upload(overrides: Partial<OtaUpload> = {}): OtaUpload {
  return { upload_id: '58d78531-6388-41e5-b0fb-2fdcf3df5032', filename: 'release.ota', size: 10, sha256: 'a'.repeat(64), offset: 0, expires_at: 1, state: 'uploading', chunk_size: 4, ...overrides }
}

describe('OTA client pipeline', () => {
  it('reads the bounded manifest preview from a stored OTA container', async () => {
    const manifest = { format_version: 1, app_version: '0.2.0', compatible_from: ['0.1.0'], required_free_bytes: 536870912, changes: ['Исправления'] }
    const bytes = storedZip('manifest.json', new TextEncoder().encode(JSON.stringify(manifest)))
    const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer
    await expect(inspectOtaFile(new File([buffer], 'release.ota'))).resolves.toEqual(manifest)
    await expect(inspectOtaFile(new File([buffer], 'release.zip'))).rejects.toThrow('ota_filename_invalid')
  })

  it('hashes incrementally and reports final progress', async () => {
    const progress = vi.fn()
    await expect(hashFile(new Blob(['abc']), progress)).resolves.toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
    expect(progress).toHaveBeenLastCalledWith(3, 3)
  })

  it('resumes at the server offset and sends bounded chunks before finalizing', async () => {
    const offsets: number[] = []
    const client: OtaUploadClient = {
      create: vi.fn().mockResolvedValue(upload()), offset: vi.fn().mockResolvedValue(4),
      append: vi.fn().mockImplementation(async (_id, offset, chunk) => { offsets.push(offset); return offset + chunk.size }),
      finalize: vi.fn().mockResolvedValue(upload({ offset: 10, state: 'verified' })), remove: vi.fn(),
    }
    const progress: number[] = []
    await uploadOtaFile(new File(['0123456789'], 'release.ota'), 'a'.repeat(64), client, value => progress.push(value))
    expect(offsets).toEqual([4, 8])
    expect(progress).toEqual([4, 8, 10])
    expect(client.finalize).toHaveBeenCalledOnce()
  })
})
