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
    this.port1 = { onmessage: null, postMessage: data => queueMicrotask(() => this.port2.onmessage?.({ data })), close: () => {} }
    this.port2 = { onmessage: null, postMessage: data => queueMicrotask(() => this.port1.onmessage?.({ data })), close: () => {} }
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
        ROBOPARK_SOAK_RUN_TOKEN: 'duration-boundary-test',
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
    const packageVersion = JSON.parse(await readFile(join(webRoot, 'package.json'), 'utf8')).version
    assert.match(first, new RegExp(`const version = "${packageVersion.replaceAll('.', '\\.')}-[a-f0-9]{16}"`))
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

test('first installation precaches lazy route code and versions its bytes', async () => {
  const dist = await mkdtemp(join(tmpdir(), 'robopark-sw-routes-'))
  try {
    await mkdir(join(dist, 'assets', 'routes'), { recursive: true })
    await writeFile(join(dist, 'index.html'), '<script src="/assets/index-a1.js"></script><link href="/assets/index-b2.css">')
    for (const file of ['assets/index-a1.js', 'assets/index-b2.css', 'assets/routes/Work-a1.js', 'assets/routes/Work-b2.css', 'offline.html', 'pwa-icon-192.png', 'pwa-icon-512.png']) await writeFile(join(dist, file), file)
    await writeFile(join(dist, 'assets/private.json'), 'not a shell asset')
    const first = await buildServiceWorker(dist)
    assert.match(first, /\/assets\/routes\/Work-a1\.js/)
    assert.match(first, /\/assets\/routes\/Work-b2\.css/)
    assert.doesNotMatch(first, /private\.json/)
    await writeFile(join(dist, 'assets/routes/Work-a1.js'), 'changed lazy route')
    assert.notEqual(await buildServiceWorker(dist), first)
  } finally { await rm(dist, { recursive: true, force: true }) }
})

test('terminal HTML and its isolated bundles never enter the offline shell cache', async () => {
  const dist = await mkdtemp(join(tmpdir(), 'robopark-sw-terminal-'))
  try {
    await mkdir(join(dist, 'assets', 'terminal'), { recursive: true })
    await writeFile(join(dist, 'index.html'), '<script src="/assets/index-a1.js"></script><link href="/assets/index-b2.css">')
    await writeFile(join(dist, 'terminal.html'), '<script src="/assets/terminal/terminal-a1.js"></script>')
    for (const file of ['assets/index-a1.js', 'assets/index-b2.css', 'assets/terminal/terminal-a1.js', 'assets/terminal/terminal-b2.css', 'offline.html', 'pwa-icon-192.png', 'pwa-icon-512.png']) await writeFile(join(dist, file), file)
    const worker = await buildServiceWorker(dist)
    const precacheLine = worker.split('\n').find(line => line.startsWith('const precache = '))
    assert.doesNotMatch(precacheLine, /terminal\.html|assets\\?\/terminal/)
    const source = await readFile(new URL('./sw-template.js', import.meta.url), 'utf8')
    assert.match(source, /pathname === '\/terminal\.html'/)
  } finally { await rm(dist, { recursive: true, force: true }) }
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

test('photo share redirect carries only its own unpredictable inbox id', async () => {
  const source = await readFile(new URL('./sw-template.js', import.meta.url), 'utf8')
    .then(value => value.replace("'__CACHE_VERSION__'", '"test"').replace("['__PRECACHE__']", '["/index.html"]'))
  const handlers = new Map()
  const indexedDB = new IDBFactory()
  const shareId = '01234567-89ab-4cde-8fab-0123456789ab'
  const scope = { location: { origin: 'https://robopark.test' }, addEventListener: (name, handler) => handlers.set(name, handler) }
  vm.runInNewContext(source, { self: scope, indexedDB, Blob, Response, URL, crypto: { randomUUID: () => shareId } })
  const photo = new Blob(['image'], { type: 'image/jpeg' })
  const event = {
    request: { url: 'https://robopark.test/share-target', method: 'POST', formData: async () => ({ get: () => photo }) },
    response: null,
    respondWith(promise) { this.response = promise },
  }
  handlers.get('fetch')(event)
  const response = await event.response
  assert.equal(response.status, 303)
  assert.equal(new URL(response.headers.get('location')).searchParams.get('shared'), shareId)
  const request = indexedDB.open('robopark-share-inbox-v2')
  const db = await new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
  assert.equal(db.objectStoreNames.contains('drafts'), true)
  const transaction = db.transaction('drafts', 'readonly')
  const stored = await new Promise((resolve, reject) => {
    const item = transaction.objectStore('drafts').get(shareId)
    item.onsuccess = () => resolve(item.result)
    item.onerror = () => reject(item.error)
  })
  assert.equal(stored.ownerAccountId, null)
  db.close()
})

test('share worker bounds unclaimed photos even if the app never opens', async () => {
  const source = await readFile(new URL('./sw-template.js', import.meta.url), 'utf8')
    .then(value => value.replace("'__CACHE_VERSION__'", '"test"').replace("['__PRECACHE__']", '["/index.html"]'))
  const handlers = new Map()
  const indexedDB = new IDBFactory()
  let next = 0
  const scope = { location: { origin: 'https://robopark.test' }, addEventListener: (name, handler) => handlers.set(name, handler) }
  vm.runInNewContext(source, { self: scope, indexedDB, Blob, Response, URL,
    Date: { now: () => 1_790_456_400_000 },
    crypto: { randomUUID: () => `01234567-89ab-4cde-8fab-${String(++next).padStart(12, '0')}` } })
  for (let index = 0; index < 12; index++) {
    const event = { request: { url: 'https://robopark.test/share-target', method: 'POST',
      formData: async () => ({ get: () => new Blob(['photo'], { type: 'image/jpeg' }) }) },
    response: null, respondWith(promise) { this.response = promise } }
    handlers.get('fetch')(event)
    assert.equal((await event.response).status, 303)
  }
  const opening = indexedDB.open('robopark-share-inbox-v2')
  const db = await new Promise((resolve, reject) => {
    opening.onsuccess = () => resolve(opening.result)
    opening.onerror = () => reject(opening.error)
  })
  const transaction = db.transaction('drafts', 'readonly')
  const drafts = await new Promise((resolve, reject) => {
    const request = transaction.objectStore('drafts').getAll()
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
  assert.equal(drafts.length, 10)
  assert.equal(drafts.some(draft => draft.id.endsWith('000000000001')), false)
  db.close()
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
      if (message.type === 'PREPARE_ACTIVATION') ports[0].postMessage({ safe: true })
    } }] },
    skipWaiting: async () => { scope.skipped = true },
    skipped: false,
    addEventListener: (name, handler) => handlers.set(name, handler),
    caches: {
      open: async () => ({
        addAll: async () => {},
        match: async (request) => stored.get(typeof request === 'string' ? request : request.url)?.clone(),
        put: async (request, response) => { stored.set(typeof request === 'string' ? request : request.url, response) },
        keys: async () => [...stored.keys()].map((url) => ({ url })),
        delete: async (request) => stored.delete(request.url),
      }),
      match: async (request) => stored.get(request.url)?.clone(),
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
  assert.equal(await (await stored.get('/index.html')).clone().text(), 'Cached shell')
  // Pending work can delay activation: a second navigation must still use the
  // installed shell, whose chunks belong to this worker's version.
  assert.equal(await (await dispatch('/robots', { mode: 'navigate' }).response).clone().text(), 'Cached shell')
  assert.equal([...stored.keys()].some(key => key.includes('private') || key.includes('/api/')), false)

  const installWaits = []
  handlers.get('install')({ waitUntil: promise => installWaits.push(promise) })
  await Promise.all(installWaits)
  assert.equal(clientMessages.some(message => message.type === 'UPDATE_READY'), true)
  assert.equal(scope.skipped, true)
  const messageWaits = []
  handlers.get('message')({ data: { type: 'ACTIVATE_WHEN_SAFE', state: { status: 'idle', pending: 0, conflicts: 0 } }, waitUntil: promise => messageWaits.push(promise) })
  await Promise.all(messageWaits)
  assert.equal(scope.skipped, true)
})

for (const { clientKind, reply, navigations } of [
  { clientKind: 'modern', reply: { safe: true, reloadOnControllerChange: true }, navigations: 0 },
  { clientKind: 'previous', reply: { safe: true }, navigations: 1 },
]) test(`a waiting worker activates ${clientKind} clients with one reload`, async () => {
  const source = await readFile(new URL('./sw-template.js', import.meta.url), 'utf8')
    .then(value => value.replace("'__CACHE_VERSION__'", '"next"').replace("['__PRECACHE__']", '["/index.html"]'))
  const handlers = new Map()
  let skipped = 0
  let navigated = 0
  const scope = {
    location: { origin: 'https://robopark.test' },
    clients: {
      claim: async () => {},
      matchAll: async () => [{ id: 'open-client', url: 'https://robopark.test/login', navigate: async () => { navigated += 1 }, postMessage: (message, ports) => {
        if (message.type === 'PREPARE_ACTIVATION') ports[0].postMessage(reply)
      } }],
    },
    skipWaiting: async () => { skipped += 1 },
    addEventListener: (name, handler) => handlers.set(name, handler),
  }
  const caches = { open: async () => ({ addAll: async () => {} }), keys: async () => [], delete: async () => true }
  vm.runInNewContext(source, {
    self: scope, caches, indexedDB: new IDBFactory(), MessageChannel: TestMessageChannel,
    setTimeout, clearTimeout,
  })

  const waits = []
  handlers.get('install')({ waitUntil: promise => waits.push(promise) })
  await Promise.all(waits)

  assert.equal(skipped, 1)
  const activateWaits = []
  handlers.get('activate')({ waitUntil: promise => activateWaits.push(promise) })
  await Promise.all(activateWaits)
  assert.equal(navigated, navigations)
})

test('a client opened after the activation snapshot reloads under the new worker', async () => {
  const source = await readFile(new URL('./sw-template.js', import.meta.url), 'utf8')
    .then(value => value.replace("'__CACHE_VERSION__'", '"next"').replace("['__PRECACHE__']", '["/index.html"]'))
  const handlers = new Map()
  let navigated = 0
  const prepared = {
    id: 'prepared-client',
    url: 'https://robopark.test/overview',
    navigate: async () => { navigated += 1 },
    postMessage: (message, ports) => {
      if (message.type === 'PREPARE_ACTIVATION') {
        ports[0].postMessage({ safe: true, reloadOnControllerChange: true })
      }
    },
  }
  const late = {
    id: 'late-client',
    url: 'https://robopark.test/login',
    navigate: async () => { navigated += 1 },
    postMessage: () => {},
  }
  let windows = [prepared]
  const scope = {
    location: { origin: 'https://robopark.test' },
    clients: {
      claim: async () => {},
      matchAll: async () => windows,
    },
    skipWaiting: async () => { windows = [prepared, late] },
    addEventListener: (name, handler) => handlers.set(name, handler),
  }
  const caches = { open: async () => ({ addAll: async () => {} }), keys: async () => [], delete: async () => true }
  vm.runInNewContext(source, {
    self: scope, caches, indexedDB: new IDBFactory(), MessageChannel: TestMessageChannel,
    setTimeout, clearTimeout,
  })

  const installWaits = []
  handlers.get('install')({ waitUntil: promise => installWaits.push(promise) })
  await Promise.all(installWaits)
  const activateWaits = []
  handlers.get('activate')({ waitUntil: promise => activateWaits.push(promise) })
  await Promise.all(activateWaits)

  assert.equal(navigated, 1)
})

test('a waiting worker retries activation while an existing client finishes authentication', async () => {
  const source = await readFile(new URL('./sw-template.js', import.meta.url), 'utf8')
    .then(value => value.replace("'__CACHE_VERSION__'", '"next"').replace("['__PRECACHE__']", '["/index.html"]'))
  const handlers = new Map()
  let attempts = 0
  let skipped = 0
  const client = { id: 'login-client', postMessage: (message, ports) => {
    if (message.type === 'PREPARE_ACTIVATION') {
      attempts += 1
      ports[0].postMessage({ safe: attempts > 1 })
    }
  } }
  const scope = {
    location: { origin: 'https://robopark.test' },
    clients: { matchAll: async () => [client] },
    skipWaiting: async () => { skipped += 1 },
    addEventListener: (name, handler) => handlers.set(name, handler),
  }
  vm.runInNewContext(source, {
    self: scope, caches: { open: async () => ({ addAll: async () => {} }) },
    indexedDB: new IDBFactory(), MessageChannel: TestMessageChannel, setTimeout, clearTimeout,
  })

  const waits = []
  handlers.get('install')({ waitUntil: promise => waits.push(promise) })
  await Promise.all(waits)

  assert.equal(attempts, 1)
  assert.equal(skipped, 0)
  const ready = []
  handlers.get('message')({ data: { type: 'ACTIVATE_WHEN_SAFE', state: { status: 'idle', pending: 0, conflicts: 0 } }, waitUntil: promise => ready.push(promise) })
  await Promise.all(ready)
  assert.equal(attempts, 2)
  assert.equal(skipped, 1)
})

test('a concurrent activation request retries after the in-flight client handshake releases', async () => {
  const source = await readFile(new URL('./sw-template.js', import.meta.url), 'utf8')
    .then(value => value.replace("'__CACHE_VERSION__'", '"next"').replace("['__PRECACHE__']", '["/index.html"]'))
  const handlers = new Map()
  const timers = new Map()
  let timerId = 0
  let skipped = 0
  let fenced = false
  let clients
  let mainAttempts = 0
  let silentPrepared
  const silentWasPrepared = new Promise(resolve => { silentPrepared = resolve })
  const main = { id: 'main', postMessage: (message, ports) => {
    if (message.type !== 'PREPARE_ACTIVATION') return
    mainAttempts += 1
    const port = ports[0]
    const safe = !fenced
    if (safe) fenced = true
    port.onmessage = event => {
      if (event.data?.type === 'RELEASE_ACTIVATION') {
        fenced = false
        port.postMessage({ type: 'RELEASED_ACTIVATION' })
      }
    }
    port.postMessage({ safe, reloadOnControllerChange: true })
  } }
  const silent = { id: 'auth-loading', postMessage: message => {
    if (message.type === 'PREPARE_ACTIVATION') silentPrepared()
  } }
  clients = [main, silent]
  const scope = {
    location: { origin: 'https://robopark.test' },
    clients: { matchAll: async () => clients },
    skipWaiting: async () => { skipped += 1 },
    addEventListener: (name, handler) => handlers.set(name, handler),
  }
  vm.runInNewContext(source, {
    self: scope, indexedDB: new IDBFactory(), MessageChannel: TestMessageChannel,
    setTimeout: callback => { const id = ++timerId; timers.set(id, callback); return id },
    clearTimeout: id => timers.delete(id),
  })
  const request = () => {
    const waits = []
    handlers.get('message')({ data: { type: 'ACTIVATE_WHEN_SAFE', state: { status: 'idle', pending: 0, conflicts: 0 } }, waitUntil: promise => waits.push(promise) })
    return Promise.all(waits)
  }

  const inFlight = request()
  await silentWasPrepared
  clients = [main]
  const finalRequest = request()
  for (let turn = 0; turn < 10 && mainAttempts < 2; turn += 1) {
    await new Promise(resolve => setImmediate(resolve))
  }
  for (const timeout of [...timers.values()]) timeout()
  for (let turn = 0; turn < 10 && mainAttempts < 2; turn += 1) {
    await new Promise(resolve => setImmediate(resolve))
    for (const timeout of [...timers.values()]) timeout()
  }
  let watchdog
  try {
    await Promise.race([
      Promise.all([inFlight, finalRequest]),
      new Promise((_, reject) => { watchdog = setTimeout(() => reject(new Error(`activation stuck: attempts=${mainAttempts}, timers=${timers.size}, fenced=${fenced}`)), 100) }),
    ])
  } finally { clearTimeout(watchdog) }

  assert.equal(skipped, 1, 'the final safe request is retried after the earlier fence releases')
})

test('a released rc.16 client without an acknowledgement cannot stall the activation queue', async () => {
  const source = await readFile(new URL('./sw-template.js', import.meta.url), 'utf8')
    .then(value => value.replace("'__CACHE_VERSION__'", '"next"').replace("['__PRECACHE__']", '["/index.html"]'))
  const handlers = new Map()
  let skipped = 0
  const client = { id: 'rc16-client', postMessage: (message, ports) => {
    if (message.type === 'PREPARE_ACTIVATION') ports[0].postMessage({ safe: false })
  } }
  const scope = {
    location: { origin: 'https://robopark.test' },
    clients: { matchAll: async () => [client] },
    skipWaiting: async () => { skipped += 1 },
    addEventListener: (name, handler) => handlers.set(name, handler),
  }
  vm.runInNewContext(source, {
    self: scope, indexedDB: new IDBFactory(), MessageChannel: TestMessageChannel,
    setTimeout: (callback, delay) => { if (delay === 250) queueMicrotask(callback); return delay },
    clearTimeout: () => {},
  })
  const waits = []
  handlers.get('message')({ data: { type: 'ACTIVATE_WHEN_SAFE', state: { status: 'idle', pending: 0, conflicts: 0 } }, waitUntil: promise => waits.push(promise) })

  await Promise.all(waits)
  assert.equal(skipped, 0)
})

test('a client veto during the prepared handshake still blocks activation', async () => {
  const source = await readFile(new URL('./sw-template.js', import.meta.url), 'utf8')
    .then(value => value.replace("'__CACHE_VERSION__'", '"next"').replace("['__PRECACHE__']", '["/index.html"]'))
  const handlers = new Map()
  let skipped = 0
  const client = { id: 'editing-client', postMessage: (message, ports) => {
    if (message.type !== 'PREPARE_ACTIVATION') return
    const port = ports[0]
    port.onmessage = event => {
      if (event.data?.type === 'RELEASE_ACTIVATION') port.postMessage({ type: 'RELEASED_ACTIVATION' })
    }
    port.postMessage({ safe: true })
    port.postMessage({ type: 'VETO_ACTIVATION' })
  } }
  const scope = {
    location: { origin: 'https://robopark.test' },
    clients: { matchAll: async () => [client] },
    skipWaiting: async () => { skipped += 1 },
    addEventListener: (name, handler) => handlers.set(name, handler),
  }
  vm.runInNewContext(source, {
    self: scope, indexedDB: new IDBFactory(), MessageChannel: TestMessageChannel,
    setTimeout, clearTimeout,
  })
  const waits = []
  handlers.get('message')({ data: { type: 'ACTIVATE_WHEN_SAFE', state: { status: 'idle', pending: 0, conflicts: 0 } }, waitUntil: promise => waits.push(promise) })

  await Promise.all(waits)
  assert.equal(skipped, 0)
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
  let onChallenge = null
  const scope = {
    location: { origin: 'https://robopark.test' },
    clients: { claim: async () => {}, matchAll: async () => [{ postMessage: (_message, ports) => {
      void Promise.resolve(onChallenge?.()).then(() => ports[0].postMessage({ safe: clientSafe }))
    } }] },
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
  onChallenge = () => write('actions', { dbId: 'scope-a\0action', scope: 'scope-a', state: 'ready' })
  await message({ status: 'idle', pending: 0, conflicts: 0 })
  assert.equal(skipped, 0, 'a durable action created during the handshake vetoes activation')
  await write('actions', { dbId: 'scope-a\0action', scope: 'scope-a', state: 'confirmed' })
  onChallenge = null
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

test('silent client cannot force a reload while in-memory work may exist', async () => {
  const source = await readFile(new URL('./sw-template.js', import.meta.url), 'utf8')
    .then(value => value.replace("'__CACHE_VERSION__'", '"test"').replace("['__PRECACHE__']", '["/index.html"]'))
  const handlers = new Map()
  let skipped = 0
  let navigated = 0
  const indexedDB = new IDBFactory()
  const opening = indexedDB.open('robopark-offline', 1)
  opening.onupgradeneeded = () => {
    for (const name of ['actions', 'media']) {
      opening.result.createObjectStore(name, { keyPath: 'id' }).createIndex('state', 'state')
    }
  }
  const db = await new Promise((resolve, reject) => {
    opening.onsuccess = () => resolve(opening.result)
    opening.onerror = () => reject(opening.error)
  })
  const setAction = async state => {
    const tx = db.transaction('actions', 'readwrite')
    tx.objectStore('actions').put({ id: 'local-work', state })
    await new Promise((resolve, reject) => {
      tx.oncomplete = resolve
      tx.onerror = () => reject(tx.error)
    })
  }
  await setAction('ready')
  const legacy = { id: 'old-pwa', type: 'window', url: 'https://robopark.test/overview', postMessage: () => {}, navigate: async () => { navigated += 1 } }
  let clients = [legacy]
  const scope = {
    location: { origin: 'https://robopark.test' },
    clients: { matchAll: async () => clients, claim: async () => {} },
    skipWaiting: async () => { skipped += 1 },
    addEventListener: (name, handler) => handlers.set(name, handler),
  }
  const caches = { keys: async () => [], delete: async () => {} }
  vm.runInNewContext(source, { self: scope, caches, indexedDB, MessageChannel: TestMessageChannel,
    setTimeout: callback => queueMicrotask(callback), clearTimeout: () => {} })
  const request = async (source, state) => {
    const waits = []
    handlers.get('message')({ source, data: { type: 'ACTIVATE_WHEN_SAFE', state }, waitUntil: promise => waits.push(promise) })
    await Promise.all(waits)
  }
  await request()
  assert.equal(skipped, 0, 'unanswered legacy client keeps unsent durable work')
  await request(legacy)
  assert.equal(skipped, 0, 'even an explicit legacy request cannot discard durable work')
  await setAction('confirmed')
  await request()
  assert.equal(skipped, 0, 'an unanswered client may still hold an unsaved form')
  await request(legacy, { status: 'idle', pending: 0, conflicts: 0 })
  assert.equal(skipped, 0, 'modern clients still have to answer the handshake')
  clients = [legacy, { ...legacy, id: 'other-tab' }]
  await request(legacy)
  assert.equal(skipped, 0, 'the requester cannot confirm a different silent tab')
  clients = [legacy]
  await request({ id: legacy.id, type: 'serviceworker' })
  assert.equal(skipped, 0, 'only a window client can request legacy activation')
  await request(legacy)
  assert.equal(skipped, 1, 'the explicit legacy button permits its own settled window to update')
  const waits = []
  handlers.get('activate')({ waitUntil: promise => waits.push(promise) })
  await Promise.all(waits)
  assert.equal(navigated, 1)
  db.close()
})

async function cacheFailureWorker(failingOperation, network = async () => new Response('Available online', {
  headers: { 'content-type': 'text/javascript' },
}), shellContents = {}) {
  const source = await readFile(new URL('./sw-template.js', import.meta.url), 'utf8')
  const handlers = new Map()
  const failAt = operation => {
    if (operation === failingOperation) throw Object.assign(new Error('Browser storage is unavailable'), { name: 'QuotaExceededError' })
  }
  const cache = {
    match: async path => { failAt('shell-match'); return shellContents[path]?.clone() },
    put: async () => { failAt('put') },
    keys: async () => { failAt('keys'); return Array.from({ length: 101 }, (_, i) => ({ url: String(i) })) },
    delete: async () => { failAt('delete'); return true },
  }
  const caches = {
    match: async () => { failAt('match'); return undefined },
    open: async () => { failAt('open'); return cache },
  }
  const scope = { location: { origin: 'https://robopark.test' }, addEventListener: (name, handler) => handlers.set(name, handler) }
  vm.runInNewContext(source, { self: scope, caches, URL, fetch: network })
  return (path, mode = 'cors') => {
    let response
    handlers.get('fetch')({
      request: { url: new URL(path, scope.location.origin).href, method: 'GET', mode },
      respondWith: promise => { response = promise },
    })
    return response
  }
}

for (const operation of ['match', 'open', 'put', 'keys', 'delete']) {
  test(`a runtime cache ${operation} failure cannot discard a successful network response`, async () => {
    const dispatch = await cacheFailureWorker(operation)
    const response = await dispatch('/assets/photo-a1.webp')
    assert.equal(response.status, 200)
    assert.equal(await response.text(), 'Available online')
  })
}

for (const operation of ['open', 'shell-match']) {
  test(`navigation stays online when shell cache ${operation} fails`, async () => {
    const dispatch = await cacheFailureWorker(operation)
    assert.equal(await (await dispatch('/work', 'navigate')).text(), 'Available online')
  })
}

test('an unavailable cache does not hide a real network failure', async () => {
  const offline = new TypeError('Network offline')
  const dispatch = await cacheFailureWorker('match', async () => { throw offline })
  await assert.rejects(dispatch('/assets/photo-a1.webp'), error => error === offline)
})

test('terminal assets bypass the runtime cache as well as the offline precache', async () => {
  const dispatch = await cacheFailureWorker('open')
  for (const path of ['/terminal.html', '/assets/terminal/terminal-a1.js', '/assets/terminal/terminal-b2.css']) {
    assert.equal(dispatch(path), undefined, path)
  }
})

for (const operation of ['open', 'shell-match', undefined]) {
  test(`navigation preserves network failure when no offline shell can be read (${operation ?? 'empty cache'})`, async () => {
    const offline = new TypeError('Network offline')
    const dispatch = await cacheFailureWorker(operation, async () => { throw offline })
    await assert.rejects(dispatch('/work', 'navigate'), error => error === offline)
  })
}

test('navigation can still use the cached offline page after a network failure', async () => {
  const dispatch = await cacheFailureWorker(undefined, async () => { throw new TypeError('Network offline') }, {
    '/offline.html': new Response('Offline page'),
  })
  assert.equal(await (await dispatch('/work', 'navigate')).text(), 'Offline page')
})

for (const status of [401, 503]) {
  test(`navigation preserves an HTTP ${status} response when the shell cache is unavailable`, async () => {
    const response = new Response('Server response', { status })
    const dispatch = await cacheFailureWorker('shell-match', async () => response, {
      '/offline.html': new Response('Offline page'),
    })
    assert.equal(await dispatch('/work', 'navigate'), response)
  })
}
