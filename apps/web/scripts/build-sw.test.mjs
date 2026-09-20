import assert from 'node:assert/strict'
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import vm from 'node:vm'
import { buildServiceWorker } from './build-sw.mjs'

test('worker version follows built bytes and requires all precache files', async () => {
  const dist = await mkdtemp(join(tmpdir(), 'robopark-sw-'))
  try {
    await mkdir(join(dist, 'assets'))
    await writeFile(join(dist, 'index.html'), '<script type="module" src="/assets/index-a1.js"></script><link rel="stylesheet" href="/assets/index-b2.css">')
    for (const file of ['assets/index-a1.js', 'assets/index-b2.css', 'offline.html', 'pwa-icon-192.png', 'pwa-icon-512.png']) {
      await writeFile(join(dist, file), file)
    }
    const first = await buildServiceWorker(dist)
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
    clients: { claim: async () => {}, matchAll: async () => [{ postMessage: (message) => clientMessages.push(message) }] },
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
  }, Response, indexedDB: undefined })
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
  handlers.get('message')({ data: { type: 'ACTIVATE_WHEN_SAFE' } })
  await new Promise(resolve => setTimeout(resolve, 0))
  assert.equal(scope.skipped, true)
})
