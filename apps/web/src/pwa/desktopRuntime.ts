const INSTALLATION_ID_KEY = 'robopark:installation-id:v1'
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

export type DesktopBootstrap = { version: 1; instanceId: string }
export type DesktopCiphertext = {
  __roboparkProtected: true
  version: 1
  algorithm: 'A256GCM'
  instanceId: string
  iv: string
  ciphertext: string
}
type RuntimeOptions = { location?: Pick<Location | URL, 'protocol' | 'hostname' | 'port'>; purpose?: string }

declare global {
  interface Window { __ROBOPARK_DESKTOP__?: unknown }
}
let runtimeForTests: DesktopBootstrap | null | undefined

function runtimeLocation(options?: RuntimeOptions) {
  return options?.location ?? window.location
}

export function isDesktopLoopbackOrigin(options?: RuntimeOptions): boolean {
  if (typeof window === 'undefined' && !options?.location) return false
  const location = runtimeLocation(options)
  return location.protocol === 'http:' && location.hostname === '127.0.0.1' && location.port === '18444'
}

export function desktopRuntime(options?: RuntimeOptions): DesktopBootstrap | null {
  if (runtimeForTests !== undefined) return runtimeForTests
  if (typeof window === 'undefined') return null
  const value = window.__ROBOPARK_DESKTOP__ as Partial<DesktopBootstrap> | undefined
  if (!isDesktopLoopbackOrigin(options)) return null
  if (value?.version !== 1 || typeof value.instanceId !== 'string' || !UUID.test(value.instanceId)) return null
  return { version: 1, instanceId: value.instanceId.toLowerCase() }
}

export function getInstallationDeviceId(storage: Pick<Storage, 'getItem' | 'setItem'> = localStorage, options?: RuntimeOptions): string {
  const native = desktopRuntime(options)
  try {
    const storageKey = native ? `${INSTALLATION_ID_KEY}:mac:${native.instanceId}` : INSTALLATION_ID_KEY
    const prefix = native ? 'mac' : 'web'
    const existing = storage.getItem(storageKey)
    if (existing && UUID.test(existing)) return `${prefix}-${existing.toLowerCase()}`
    const created = crypto.randomUUID()
    storage.setItem(storageKey, created)
    return `${prefix}-${created}`
  } catch {
    return `${native ? 'mac' : 'web'}-${crypto.randomUUID()}`
  }
}

function encode(value: Uint8Array): string {
  let binary = ''
  for (const byte of value) binary += String.fromCharCode(byte)
  return btoa(binary)
}

function decode(value: string, maxEncodedChars: number): Uint8Array<ArrayBuffer> {
  if (!/^[A-Za-z0-9+/]+={0,2}$/.test(value) || value.length > maxEncodedChars) throw new Error('desktop_storage_envelope_invalid')
  const binary = atob(value)
  const bytes = new Uint8Array(new ArrayBuffer(binary.length))
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index)
  return bytes
}

let cachedKey: { instanceId: string; key: CryptoKey } | null = null
let pendingKey: Promise<CryptoKey> | null = null

async function storageKey(runtime: DesktopBootstrap): Promise<CryptoKey> {
  if (cachedKey?.instanceId === runtime.instanceId) return cachedKey.key
  if (pendingKey) return pendingKey
  pendingKey = (async () => {
    try {
      const response = await fetch('/__robopark/storage-key', {
        method: 'GET', credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' },
      })
      if (!response.ok) throw new Error('desktop_storage_key_unavailable')
      const body = await response.json() as { key?: unknown }
      if (typeof body.key !== 'string') throw new Error('desktop_storage_key_invalid')
      const bytes = decode(body.key, 256)
      if (bytes.byteLength !== 32) throw new Error('desktop_storage_key_invalid')
      const key = await crypto.subtle.importKey('raw', bytes, 'AES-GCM', false, ['encrypt', 'decrypt'])
      cachedKey = { instanceId: runtime.instanceId, key }
      return key
    } catch (error) {
      if (error instanceof Error && error.message.startsWith('desktop_storage_key_')) throw error
      throw new Error('desktop_storage_key_unavailable')
    } finally { pendingKey = null }
  })()
  return pendingKey
}

export async function sealDesktopJson(value: unknown, options?: RuntimeOptions): Promise<unknown> {
  const runtime = desktopRuntime(options)
  if (!runtime) return value
  const iv = crypto.getRandomValues(new Uint8Array(new ArrayBuffer(12)))
  const plaintext = new TextEncoder().encode(JSON.stringify(value))
  const additionalData = new TextEncoder().encode(`robopark-offline-v1\0${runtime.instanceId}\0${options?.purpose ?? 'resource'}`)
  const ciphertext = await crypto.subtle.encrypt({ name: 'AES-GCM', iv, additionalData }, await storageKey(runtime), plaintext)
  return {
    __roboparkProtected: true, version: 1, algorithm: 'A256GCM', instanceId: runtime.instanceId,
    iv: encode(iv), ciphertext: encode(new Uint8Array(ciphertext)),
  } satisfies DesktopCiphertext
}

export async function unsealDesktopJson(value: unknown, options?: RuntimeOptions): Promise<unknown> {
  const runtime = desktopRuntime(options)
  if (!runtime) return value
  const envelope = value as Partial<DesktopCiphertext> | null
  if (!envelope || envelope.__roboparkProtected !== true || envelope.version !== 1 || envelope.algorithm !== 'A256GCM'
    || envelope.instanceId !== runtime.instanceId || typeof envelope.iv !== 'string' || typeof envelope.ciphertext !== 'string') {
    throw new Error('desktop_storage_envelope_invalid')
  }
  const iv = decode(envelope.iv, 64)
  if (iv.byteLength !== 12) throw new Error('desktop_storage_envelope_invalid')
  try {
    const additionalData = new TextEncoder().encode(`robopark-offline-v1\0${runtime.instanceId}\0${options?.purpose ?? 'resource'}`)
    const plaintext = await crypto.subtle.decrypt(
      { name: 'AES-GCM', iv, additionalData },
      await storageKey(runtime),
      decode(envelope.ciphertext, 64 * 1024 * 1024),
    )
    return JSON.parse(new TextDecoder().decode(plaintext))
  } catch (error) {
    if (error instanceof Error && error.message.startsWith('desktop_storage_key_')) throw error
    throw new Error('desktop_storage_decryption_failed')
  }
}

const LOCAL_DRAFT_PURPOSE = 'protected-local-draft'
const localDraftGenerations = new Map<string, number>()
const localDraftWrites = new Map<string, Promise<void>>()

export async function readProtectedLocalValue(key: string): Promise<string | null> {
  await localDraftWrites.get(key)?.catch(() => undefined)
  const raw = localStorage.getItem(key)
  if (!raw || !desktopRuntime()) return raw
  try {
    const value = await unsealDesktopJson(JSON.parse(raw), { purpose: `${LOCAL_DRAFT_PURPOSE}:${key}` })
    return typeof value === 'string' ? value : null
  } catch { return null }
}

export async function writeProtectedLocalValue(key: string, value: string): Promise<void> {
  if (!desktopRuntime()) { localStorage.setItem(key, value); return }
  const generation = (localDraftGenerations.get(key) ?? 0) + 1
  localDraftGenerations.set(key, generation)
  const previous = localDraftWrites.get(key) ?? Promise.resolve()
  const write = previous.catch(() => undefined).then(async () => {
    const protectedValue = await sealDesktopJson(value, { purpose: `${LOCAL_DRAFT_PURPOSE}:${key}` })
    if (localDraftGenerations.get(key) === generation) localStorage.setItem(key, JSON.stringify(protectedValue))
  })
  localDraftWrites.set(key, write)
  try { await write } finally { if (localDraftWrites.get(key) === write) localDraftWrites.delete(key) }
}

export function removeProtectedLocalValue(key: string): void {
  localDraftGenerations.set(key, (localDraftGenerations.get(key) ?? 0) + 1)
  localStorage.removeItem(key)
}

export function resetDesktopStorageKeyForTests(): void {
  cachedKey = null; pendingKey = null; localDraftGenerations.clear(); localDraftWrites.clear()
}
export function setDesktopRuntimeForTests(value: DesktopBootstrap | null | undefined): void {
  if (import.meta.env.MODE !== 'test') throw new Error('desktop_runtime_test_override_forbidden')
  runtimeForTests = value
}
