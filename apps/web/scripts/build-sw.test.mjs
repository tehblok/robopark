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

test('worker intercepts only navigation and same-origin hashed assets', async () => {
  const source = await readFile(new URL('./sw-template.js', import.meta.url), 'utf8')
    .then((value) => value.replace("'__CACHE_VERSION__'", '"test"').replace("['__PRECACHE__']", '["/offline.html"]'))
  const handlers = new Map()
  const stored = new Map([['/offline.html', new Response('Offline')]])
  const scope = {
    location: { origin: 'https://robopark.test' },
    clients: { claim: async () => {} },
    addEventListener: (name, handler) => handlers.set(name, handler),
    caches: {
      open: async () => ({
        addAll: async () => {},
        match: async (request) => stored.get(typeof request === 'string' ? request : request.url),
        put: async (request, response) => { stored.set(request.url, response) },
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
    if (request.url.endsWith('/unavailable')) throw new Error('offline')
    return new Response('ok', { headers: { 'content-type': 'text/javascript' } })
  }, Response })
  const dispatch = (url, options = {}) => {
    const request = { url: new URL(url, scope.location.origin).href, method: options.method ?? 'GET', mode: options.mode ?? 'cors' }
    const event = { request, response: null, respondWith(promise) { this.response = promise } }
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
  const offline = dispatch('/unavailable', { mode: 'navigate' })
  assert.equal(await (await offline.response).text(), 'Offline')
  assert.equal([...stored.keys()].some(key => key.includes('private') || key.includes('/api/')), false)
  assert.doesNotMatch(source, /self\.skipWaiting\s*\(/)
})
