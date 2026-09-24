import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'
import { IDBFactory } from 'fake-indexeddb'
import { buildServiceWorker } from './build-sw.mjs'

const webRoot = join(dirname(fileURLToPath(import.meta.url)), '..')

class TestMessageChannel {
  constructor() {
    this.port1 = { onmessage: null, close: () => {} }
    this.port2 = { postMessage: data => queueMicrotask(() => this.port1.onmessage?.({ data })), close: () => {} }
  }
}

test('regular browser discovery excludes the opt-in soak suite', () => {
  const result = spawnSync('npx', ['playwright', 'test', '--list', '--project=chromium'], {
    cwd: webRoot,
    encoding: 'utf8',
  })
  assert.equal(result.status, 0, result.stderr)
  assert.doesNotMatch(result.stdout, /soak\.spec\.ts/)
})

test('soak command refuses to start without explicit duration and output', () => {
  const result = spawnSync('/bin/bash', ['scripts/playwright-linux.sh', 'soak'], {
    cwd: webRoot,
    encoding: 'utf8',
    env: { ...process.env, ROBOPARK_SOAK_DURATION_SECONDS: '', ROBOPARK_SOAK_OUTPUT: '' },
  })
  assert.equal(result.status, 2)
  assert.match(result.stderr, /ROBOPARK_SOAK_DURATION_SECONDS/)
  assert.match(result.stderr, /ROBOPARK_SOAK_OUTPUT/)
})

test('soak command rejects every zero or non-finite duration before Docker setup', () => {
  for (const duration of ['0', '0.0', '00', '000.000', 'NaN', 'Infinity', '-1']) {
    const result = spawnSync('/bin/bash', ['scripts/playwright-linux.sh', 'soak'], {
      cwd: webRoot,
      encoding: 'utf8',
      env: {
        ...process.env,
        PATH: dirname(process.execPath),
        ROBOPARK_SOAK_DURATION_SECONDS: duration,
        ROBOPARK_SOAK_OUTPUT: 'tmp/soak-boundary.json',
      },
    })
    assert.equal(result.status, 2, `duration ${duration}: ${result.stderr}`)
    assert.match(result.stderr, /positive finite number/, `duration ${duration}`)
    assert.doesNotMatch(result.stderr, /docker is required/, `duration ${duration}`)
  }
})

test('production PWA discovery is isolated from mocked browser journeys', () => {
  const result = spawnSync('npx', ['playwright', 'test', '--list', '--config=playwright.pwa.config.ts'], {
    cwd: webRoot,
    encoding: 'utf8',
  })
  assert.equal(result.status, 0, result.stderr)
  assert.match(result.stdout, /pwa-production\.spec\.ts/)
  assert.doesNotMatch(result.stdout, /operational\/|pwa-offline\.spec\.ts/)
})

test('worker version follows built bytes and requires all precache files', async () => {
  const dist = await mkdtemp(join(tmpdir(), 'robopark-sw-'))
  try {
    await mkdir(join(dist, 'assets'))
    await writeFile(join(dist, 'index.html'), '<script type="module" src="/assets/index-a1.js"></script><link rel="stylesheet" href="/assets/index-b2.css">')
    for (const file of ['assets/index-a1.js', 'assets/index-b2.css', 'offline.html', 'pwa-icon-192.png', 'pwa-icon-512.png']) {
      await writeFile(join(dist, file), file)
    }
    const first = await buildServiceWorker(dist)
    assert.match(first, /const version = "0\.2\.0-rc\.6-[a-f0-9]{16}"/)
    assert.match(first, /\/assets\/index-a1\.js/)
    assert.match(first, /\/index\.html/)
    assert.match(first, /\/offline\.html/)
    assert.equal(await readFile(join(dist, 'sw.js'), 'utf8'), first)
    await writeFile(join(dist, 'assets/index-a1.js'), 'changed bytes')
    assert.notEqual(await buildServiceWorker(dist), first)
    await rm(join(dist, 'offline.html'))
    await assert.rejects(buildServiceWorker(dist), /offline\.html/)
  } finally {
    await rm(dist, { recursive: true, force: true })
  }
})

test('manifest exposes work shortcuts and a same-origin photo share target', async () => {
  const manifest = JSON.parse(await readFile(new URL('../public/manifest.webmanifest', import.meta.url), 'utf8'))
  assert.deepEqual(manifest.shortcuts.map((shortcut) => shortcut.url), [
    '/work?view=mine',
    '/robots?scan=1',
    '/inventory',
  ])
  assert.deepEqual(manifest.share_target, {
    action: '/share-target',
    method: 'POST',
    enctype: 'multipart/form-data',
    params: { title: 'title', text: 'text', url: 'url', files: [{ name: 'photo', accept: ['image/*'] }] },
  })
})

test('worker serves the cached application shell immediately and never caches API responses', async () => {
  const source = await readFile(new URL('./sw-template.js', import.meta.url), 'utf8')
    .then((value) => value.replace("'__CACHE_VERSION__'", '"test"').replace("['__PRECACHE__']", '["/index.html", "/offline.html"]'))
  const handlers = new Map()
  const stored = new Map([
    ['/index.html', new Response('Cached shell', { headers: { 'content-type': 'text/html' } })],
    ['/offline.html', new Response('Offline')],
  ])
  const clientMessages = []
  const scope = {
    location: { origin: 'https://robopark.test' },
    clients: { claim: async () => {}, matchAll: async () => [{ postMessage: (message, ports) => {
      clientMessages.push(message)
      if (message.type === 'CHECK_ACTIVATION_SAFETY') ports[0].postMessage({ safe: true })
    } }] },
    skipWaiting: async () => { scope.skipped = true },
    skipped: false,
    addEventListener: (name, handler) => handlers.set(name, handler),
    caches: {
      open: async () => ({
        addAll: async () => {},
        match: async (request) => stored.get(typeof request === 'string' ? request : request.url),
        put: async (request, response) => { stored.set(typeof request === 'string' ? request : request.url, response) },
        keys: async () => [...stored.keys()].map((url) => ({ url })),
        delete: async (request) => stored.delete(request.url),
      }),
      match: async (request) => stored.get(request.url),
      keys: async () => [],
      delete: async () => true,
    },
  }
  let networkCalls = 0
  vm.runInNewContext(source, { self: scope, caches: scope.caches, URL, fetch: async (request) => {
    networkCalls += 1
    const url = typeof request === 'string' ? request : request.url
    if (url.endsWith('/index.html')) return new Response('Fresh shell', { headers: { 'content-type': 'text/html' } })
    return new Response('ok', { headers: { 'content-type': 'text/javascript' } })
  }, Response, indexedDB: new IDBFactory(), MessageChannel: TestMessageChannel, setTimeout, clearTimeout })
  const dispatch = (url, options = {}) => {
    const request = { url: new URL(url, scope.location.origin).href, method: options.method ?? 'GET', mode: options.mode ?? 'cors' }
    const waits = []
    const event = { request, response: null, waits, respondWith(promise) { this.response = promise }, waitUntil(promise) { waits.push(promise) } }
    handlers.get('fetch')(event)
    return event
  }
  for (const url of ['/api/auth/me', '/api/tracker/issues', '/api/attachments/photo.png', '/attachments/private-a1.png', 'https://tile.openstreetmap.org/1/1/1.png']) {
    assert.equal(dispatch(url).response, null)
  }
  assert.equal(dispatch('/assets/index-a1.js', { method: 'POST' }).response, null)
  assert.equal(dispatch('/assets/unhashed.js').response, null)
  assert.equal(dispatch('/assets/index-a1.js?private=1').response, null)
  const asset = dispatch('/assets/index-a1.js')
  assert.equal((await asset.response).status, 200)
  assert.equal(networkCalls, 1)
  await dispatch('/assets/index-a1.js').response
  assert.equal(networkCalls, 1)
  const navigation = dispatch('/work/SDCFLEETOPS-1', { mode: 'navigate' })
  assert.equal(await (await navigation.response).text(), 'Cached shell')
  await Promise.all(navigation.waits)
  assert.equal(await (await stored.get('/index.html')).text(), 'Fresh shell')
  assert.equal([...stored.keys()].some(key => key.includes('private') || key.includes('/api/')), false)

  const installWaits = []
  handlers.get('install')({ waitUntil: promise => installWaits.push(promise) })
  await Promise.all(installWaits)
  assert.equal(clientMessages.length, 1)
  assert.equal(clientMessages[0].type, 'UPDATE_READY')
  assert.equal(scope.skipped, false)
  const messageWaits = []
  handlers.get('message')({ data: { type: 'ACTIVATE_WHEN_SAFE', state: { status: 'idle', pending: 0, conflicts: 0 } }, waitUntil: promise => messageWaits.push(promise) })
  await Promise.all(messageWaits)
  assert.equal(scope.skipped, true)
})

test('waiting worker keeps scoped records and old shell until every local action and photo is settled', async () => {
  const source = await readFile(new URL('./sw-template.js', import.meta.url), 'utf8')
    .then(value => value.replace("'__CACHE_VERSION__'", '"0.2.0-rc.6-test"').replace("['__PRECACHE__']", '["/index.html"]'))
  const indexedDB = new IDBFactory()
  const opening = indexedDB.open('robopark-offline', 2)
  opening.onupgradeneeded = () => {
    for (const name of ['actions', 'media', 'entities', 'meta', 'revisions']) {
      const store = opening.result.createObjectStore(name, { keyPath: 'dbId' })
      if (name === 'actions' || name === 'media') store.createIndex('state', 'state')
    }
  }
  const db = await new Promise((resolve, reject) => {
    opening.onsuccess = () => resolve(opening.result)
    opening.onerror = () => reject(opening.error)
  })
  const write = async (store, record) => {
    const transaction = db.transaction(store, 'readwrite')
    transaction.objectStore(store).put(record)
    await new Promise((resolve, reject) => {
      transaction.oncomplete = resolve
      transaction.onerror = () => reject(transaction.error)
    })
  }
  await write('actions', { dbId: 'scope-a\0action', scope: 'scope-a', state: 'ready' })
  await write('media', { dbId: 'scope-b\0photo', scope: 'scope-b', state: 'attention', blob: new Blob(['photo']) })
  await write('entities', { dbId: 'scope-b\0draft', scope: 'scope-b', data: { value: 'keep' } })
  const names = new Set(['robopark-shell-old', 'robopark-runtime-old', 'robopark-shell-0.2.0-rc.6-test', 'unrelated-cache'])
  const handlers = new Map()
  let skipped = 0
  let clientSafe = false
  const scope = {
    location: { origin: 'https://robopark.test' },
    clients: { claim: async () => {}, matchAll: async () => [{ postMessage: (_message, ports) => ports[0].postMessage({ safe: clientSafe }) }] },
    skipWaiting: async () => { skipped += 1 },
    addEventListener: (name, handler) => handlers.set(name, handler),
  }
  const caches = { keys: async () => [...names], delete: async name => names.delete(name) }
  vm.runInNewContext(source, { self: scope, caches, indexedDB, URL, Response, Blob, MessageChannel: TestMessageChannel, setTimeout, clearTimeout })
  const message = async state => {
    const waits = []
    handlers.get('message')({ data: { type: 'ACTIVATE_WHEN_SAFE', state }, waitUntil: promise => waits.push(promise) })
    await Promise.all(waits)
  }
  await message({ status: 'idle', pending: 0, conflicts: 0 })
  assert.equal(skipped, 0)
  assert.equal(names.has('robopark-shell-old'), true)
  await write('actions', { dbId: 'scope-a\0action', scope: 'scope-a', state: 'confirmed' })
  await message({ status: 'idle', pending: 0, conflicts: 0 })
  assert.equal(skipped, 0)
  await write('media', { dbId: 'scope-b\0photo', scope: 'scope-b', state: 'confirmed', blob: new Blob(['photo']) })
  await message({ status: 'attention', pending: 0, conflicts: 0 })
  assert.equal(skipped, 0)
  await message({ status: 'idle', pending: 0, conflicts: 0 })
  assert.equal(skipped, 0)
  clientSafe = true
  await message({ status: 'idle', pending: 0, conflicts: 0 })
  assert.equal(skipped, 1)
  const waits = []
  handlers.get('activate')({ waitUntil: promise => waits.push(promise) })
  await Promise.all(waits)
  assert.deepEqual([...names].sort(), ['robopark-shell-0.2.0-rc.6-test', 'unrelated-cache'])
  const read = async store => {
    const request = db.transaction(store, 'readonly').objectStore(store).getAll()
    return new Promise((resolve, reject) => {
      request.onsuccess = () => resolve(request.result)
      request.onerror = () => reject(request.error)
    })
  }
  assert.equal((await read('actions')).length, 1)
  assert.equal((await read('media')).length, 1)
  assert.deepEqual((await read('entities'))[0].data, { value: 'keep' })
  assert.equal(db.version, 2)
  db.close()
})
