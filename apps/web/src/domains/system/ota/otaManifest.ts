import { OTA_MAX_BYTES, type OtaManifestPreview } from './otaTypes'

const EOCD = 0x06054b50
const CENTRAL = 0x02014b50
const LOCAL = 0x04034b50
const MAX_TAIL = 65_557
const MAX_MANIFEST = 4 * 1024 * 1024
const MAX_ENTRIES = 20_000

function fail(): never { throw new Error('ota_invalid_container') }
function u16(view: DataView, offset: number): number { return view.getUint16(offset, true) }
function u32(view: DataView, offset: number): number { return view.getUint32(offset, true) }

export async function inspectOtaFile(file: File): Promise<OtaManifestPreview> {
  if (!file.name.toLowerCase().endsWith('.ota')) throw new Error('ota_filename_invalid')
  if (file.size <= 0 || file.size > OTA_MAX_BYTES) throw new Error('ota_package_too_large')
  const head = new Uint8Array(await file.slice(0, 4).arrayBuffer())
  if (head.length !== 4 || head[0] !== 0x50 || head[1] !== 0x4b || head[2] !== 0x03 || head[3] !== 0x04) fail()

  const tailStart = Math.max(0, file.size - MAX_TAIL)
  const tail = await file.slice(tailStart).arrayBuffer()
  const tailView = new DataView(tail)
  let eocd = -1
  for (let index = tailView.byteLength - 22; index >= 0; index -= 1) {
    if (u32(tailView, index) === EOCD) { eocd = index; break }
  }
  if (eocd < 0 || u16(tailView, eocd + 4) !== 0 || u16(tailView, eocd + 6) !== 0) fail()
  const entries = u16(tailView, eocd + 10)
  const centralSize = u32(tailView, eocd + 12)
  const centralOffset = u32(tailView, eocd + 16)
  if (!entries || entries > MAX_ENTRIES || entries === 0xffff || centralSize === 0xffffffff || centralOffset === 0xffffffff || centralOffset + centralSize > file.size) fail()

  const central = await file.slice(centralOffset, centralOffset + centralSize).arrayBuffer()
  const view = new DataView(central)
  const decoder = new TextDecoder('utf-8', { fatal: true })
  let cursor = 0
  let manifest: { size: number; compressed: number; method: number; offset: number } | null = null
  for (let count = 0; count < entries; count += 1) {
    if (cursor + 46 > view.byteLength || u32(view, cursor) !== CENTRAL) fail()
    const flags = u16(view, cursor + 8)
    const method = u16(view, cursor + 10)
    const compressed = u32(view, cursor + 20)
    const size = u32(view, cursor + 24)
    const nameLength = u16(view, cursor + 28)
    const extraLength = u16(view, cursor + 30)
    const commentLength = u16(view, cursor + 32)
    const disk = u16(view, cursor + 34)
    const offset = u32(view, cursor + 42)
    const end = cursor + 46 + nameLength + extraLength + commentLength
    if (end > view.byteLength || flags & 1 || disk !== 0 || [compressed, size, offset].includes(0xffffffff)) fail()
    const name = decoder.decode(new Uint8Array(central, cursor + 46, nameLength))
    if (name === 'manifest.json') {
      if (manifest || size > MAX_MANIFEST || method !== 0 || compressed !== size) fail()
      manifest = { size, compressed, method, offset }
    }
    cursor = end
  }
  if (!manifest) fail()
  const localHead = await file.slice(manifest.offset, manifest.offset + 30).arrayBuffer()
  const localView = new DataView(localHead)
  if (localView.byteLength !== 30 || u32(localView, 0) !== LOCAL || u16(localView, 8) !== manifest.method) fail()
  const nameLength = u16(localView, 26)
  const extraLength = u16(localView, 28)
  const localName = decoder.decode(await file.slice(manifest.offset + 30, manifest.offset + 30 + nameLength).arrayBuffer())
  if (localName !== 'manifest.json') fail()
  const dataStart = manifest.offset + 30 + nameLength + extraLength
  const raw = await file.slice(dataStart, dataStart + manifest.compressed).arrayBuffer()
  let value: unknown
  try { value = JSON.parse(decoder.decode(raw)) } catch { throw new Error('ota_manifest_invalid') }
  if (!value || typeof value !== 'object') throw new Error('ota_manifest_invalid')
  const row = value as Record<string, unknown>
  if (row.format_version !== 1 || typeof row.app_version !== 'string'
    || !Array.isArray(row.compatible_from) || !row.compatible_from.every(item => typeof item === 'string')
    || !Number.isSafeInteger(row.required_free_bytes) || Number(row.required_free_bytes) < 0
    || !Array.isArray(row.changes) || !row.changes.every(item => typeof item === 'string')) throw new Error('ota_manifest_invalid')
  return {
    format_version: 1,
    app_version: row.app_version,
    compatible_from: row.compatible_from,
    required_free_bytes: Number(row.required_free_bytes),
    changes: row.changes.slice(0, 100),
  }
}
