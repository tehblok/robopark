import { createReadStream, readFileSync } from 'node:fs'
import { readFile, stat } from 'node:fs/promises'
import { createServer } from 'node:http'
import { extname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'
import { createServer as createViteServer } from 'vite'
import { readProductionCsp } from './pwa-fixture-csp.mjs'

const port = Number(process.argv[2])
if (!Number.isInteger(port) || port < 1 || port > 65_535) throw new Error('A valid fixture port is required')

const dist = resolve(fileURLToPath(new URL('../dist', import.meta.url)))
const nginxConfig = readFileSync(new URL('../nginx.conf', import.meta.url), 'utf8')
const productionSecurityHeaders = { 'content-security-policy': readProductionCsp(nginxConfig, '/index.html') }
const terminalSecurityHeaders = { 'content-security-policy': readProductionCsp(nginxConfig, '/terminal.html') }
// Reuse the same operational fixtures through real HTTP. Browser route mocks
// cannot reliably intercept requests controlled by a service worker.
const fixtureLoader = await createViteServer({ configFile: false, server: { middlewareMode: true, watch: null }, appType: 'custom' })
const { operationalRoutes, userForRole } = await fixtureLoader.ssrLoadModule('/e2e/operational/fixtures.ts')
await fixtureLoader.close()
let scenarioUser = null
let signedIn = false
let routes = []
let holdAuth = false
const pendingAuth = new Set()
const contentTypes = new Map([
  ['.css', 'text/css; charset=utf-8'],
  ['.html', 'text/html; charset=utf-8'],
  ['.js', 'text/javascript; charset=utf-8'],
  ['.json', 'application/json; charset=utf-8'],
  ['.png', 'image/png'],
  ['.svg', 'image/svg+xml'],
  ['.webmanifest', 'application/manifest+json'],
  ['.webp', 'image/webp'],
  ['.woff2', 'font/woff2'],
])
let fixtureVersion = 'v1'
let legacyClient = false
let legacyClientSource
let legacyWorkerSource

const securityProbeSource = `
globalThis.securityProbeExternalLoaded = true
globalThis.securityProbeResult = (async () => {
  const violations = []
  document.addEventListener('securitypolicyviolation', event => violations.push({
    blockedURI: event.blockedURI,
    effectiveDirective: event.effectiveDirective,
  }))

  globalThis.securityProbeInlineRan = false
  const inlineScript = document.createElement('script')
  inlineScript.textContent = 'globalThis.securityProbeInlineRan = true'
  document.head.append(inlineScript)

  globalThis.securityProbeForeignRan = false
  const foreignScript = document.createElement('script')
  const controlPort = Number(location.port) + 1
  foreignScript.src = location.protocol + '//' + location.hostname + ':' + controlPort + '/__csp_foreign_script__.js'
  await new Promise(resolve => {
    foreignScript.onload = () => { globalThis.securityProbeForeignRan = true; resolve() }
    foreignScript.onerror = resolve
    document.head.append(foreignScript)
  })

  const image = new Image()
  const imageUrl = URL.createObjectURL(new Blob([
    '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"><rect width="1" height="1" fill="green"/></svg>',
  ], { type: 'image/svg+xml' }))
  const blobImageDecoded = await new Promise(resolve => {
    image.onload = () => resolve(image.naturalWidth === 1 && image.naturalHeight === 1)
    image.onerror = () => resolve(false)
    image.src = imageUrl
    document.body.append(image)
  })
  URL.revokeObjectURL(imageUrl)

  const object = document.createElement('object')
  object.data = '/__pwa_fixture__/security-probe-object.html'
  object.type = 'text/html'
  document.body.append(object)
  await new Promise(resolve => setTimeout(resolve, 100))
  object.remove()

  return {
    blobImageDecoded,
    externalLoaded: globalThis.securityProbeExternalLoaded,
    foreignRan: globalThis.securityProbeForeignRan,
    inlineRan: globalThis.securityProbeInlineRan,
    violations,
  }
})()
`

function legacyBundleClient() {
  if (legacyClientSource) return legacyClientSource
  const source = readFileSync(new URL('../e2e-production/fixtures/legacy-2d2ce191/registerServiceWorker.ts.txt', import.meta.url), 'utf8')
  legacyClientSource = `${ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText}
await registerServiceWorker({ production: true, secure: window.isSecureContext, serviceWorker: navigator.serviceWorker, reload: () => window.location.reload() })
document.getElementById('legacy-update').addEventListener('click', async () => {
  const registration = await navigator.serviceWorker.getRegistration('/')
  window.legacyActivationRequested = activateServiceWorkerWhenSafe(registration, { status: 'idle', pending: 0 })
})
`
  return legacyClientSource
}

function legacyBundleWorker() {
  if (legacyWorkerSource) return legacyWorkerSource
  const source = readFileSync(new URL('../e2e-production/fixtures/legacy-2d2ce191/sw-template.js.txt', import.meta.url), 'utf8')
  legacyWorkerSource = source
    .replace("'__CACHE_VERSION__'", JSON.stringify('legacy-fixture-v1'))
    .replace("['__PRECACHE__']", JSON.stringify(['/index.html', '/offline.html', '/pwa-icon-192.png', '/pwa-icon-512.png']))
  return legacyWorkerSource
}

function controlledPrivateResponse(pathname) {
  if (pathname === '/api/private-pwa-probe') return 'controlled-private-api-response'
  if (pathname === '/attachments/private-pwa-probe') return 'controlled-private-attachment-response'
  return null
}

async function resolvePublicFile(pathname) {
  const requested = pathname === '/' ? '/index.html' : pathname
  const candidate = resolve(dist, `.${requested}`)
  if (relative(dist, candidate).startsWith('..')) return null
  try {
    if ((await stat(candidate)).isFile()) return candidate
  } catch {
    // Client-side routes are served by the application shell below.
  }
  return join(dist, 'index.html')
}

async function readJson(request) {
  const chunks = []
  for await (const chunk of request) chunks.push(chunk)
  return JSON.parse(Buffer.concat(chunks).toString('utf8'))
}

function send(response, result) {
  response.writeHead(result.status ?? 200, { 'cache-control': 'no-store', 'content-type': 'application/json', ...result.headers })
  response.end(result.json !== undefined ? JSON.stringify(result.json) : result.body)
}

const server = createServer(async (request, response) => {
  try {
  const url = new URL(request.url ?? '/', 'http://127.0.0.1')
  if (request.method === 'POST' && url.pathname === '/__pwa_fixture__/version') {
    const data = await readJson(request)
    if (!['v1', 'v2'].includes(data.version)) { response.writeHead(400).end(); return }
    fixtureVersion = data.version
    legacyClient = data.legacyClient === true
    response.writeHead(204, { 'cache-control': 'no-store' }).end()
    return
  }
  if (url.pathname === '/__pwa_fixture__/legacy-client.js') {
    response.writeHead(200, { ...productionSecurityHeaders, 'cache-control': 'no-store', 'content-type': 'text/javascript; charset=utf-8' })
    response.end(legacyBundleClient())
    return
  }
  if (url.pathname === '/__pwa_fixture__/security-probe.html') {
    response.writeHead(200, { ...productionSecurityHeaders, 'cache-control': 'no-store', 'content-type': 'text/html; charset=utf-8' })
    response.end('<!doctype html><html><head><meta charset="utf-8"><title>CSP probe</title><script src="/__pwa_fixture__/security-probe.js" defer></script></head><body></body></html>')
    return
  }
  if (url.pathname === '/__pwa_fixture__/security-probe.js') {
    response.writeHead(200, { ...productionSecurityHeaders, 'cache-control': 'no-store', 'content-type': 'text/javascript; charset=utf-8' })
    response.end(securityProbeSource)
    return
  }
  // A 401 is the only authoritative anonymous identity response. Serving the
  // HTML fallback here would be a parse failure and must keep activation shut.
  if (url.pathname === '/api/auth/me') {
    if (holdAuth) { pendingAuth.add(response); response.on('close', () => pendingAuth.delete(response)); return }
    send(response, signedIn && scenarioUser ? { json: scenarioUser } : { status: 401, json: { detail: 'not_authenticated' } })
    return
  }
  const controlled = controlledPrivateResponse(url.pathname)
  if (controlled !== null) {
    response.writeHead(200, {
      'cache-control': 'private, no-store',
      'content-type': 'text/plain; charset=utf-8',
    })
    response.end(request.method === 'HEAD' ? undefined : controlled)
    return
  }
  if (url.pathname.startsWith('/api/')) {
    if (request.method === 'POST' && url.pathname === '/api/auth/login' && scenarioUser) {
      signedIn = true; send(response, { status: 204 }); return
    }
    if (request.method === 'POST' && url.pathname === '/api/auth/logout') {
      signedIn = false; send(response, { status: 204 }); return
    }
    const defaults = {
      '/api/ops/maintenance': { json: { active: false, kind: null, operator: false } },
      '/api/presence/heartbeat': { status: 204 },
      '/api/reports/badge': { json: { count: 0 } },
      '/api/parks': { json: scenarioUser?.parks ?? [] },
    }
    const route = routes.find(candidate => {
      if (candidate.method !== request.method) return false
      if (typeof candidate.path === 'string') return candidate.path === url.pathname
      candidate.path.lastIndex = 0; return candidate.path.test(url.pathname)
    })
    let result = defaults[url.pathname] ?? { status: 404, json: { detail: 'fixture_not_configured', path: url.pathname } }
    if (route) {
      const chunks = []
      for await (const chunk of request) chunks.push(chunk)
      const body = ['GET', 'HEAD'].includes(request.method) ? undefined : Buffer.concat(chunks)
      result = await route.handler(new Request(url, { method: request.method, headers: request.headers, body }))
    }
    send(response, result); return
  }

  const file = await resolvePublicFile(decodeURIComponent(url.pathname))
  if (!file) {
    response.writeHead(403).end()
    return
  }
  response.writeHead(200, {
    ...(file === join(dist, 'terminal.html') ? terminalSecurityHeaders : productionSecurityHeaders),
    'cache-control': 'no-cache',
    'content-type': contentTypes.get(extname(file)) ?? 'application/octet-stream',
  })
  if (request.method === 'HEAD') response.end()
  else if (file === join(dist, 'index.html')) {
    if (legacyClient) {
      response.end(`<!doctype html><html><head><meta name="pwa-fixture-version" content="${fixtureVersion}"></head><body><button id="legacy-update">Установить обновление</button><script type="module" src="/__pwa_fixture__/legacy-client.js"></script></body></html>`)
      return
    }
    const source = await readFile(file, 'utf8')
    response.end(source.replace('</head>', `<meta name="pwa-fixture-version" content="${fixtureVersion}"></head>`))
  } else if (file === join(dist, 'sw.js')) {
    if (legacyClient && fixtureVersion === 'v1') {
      response.end(legacyBundleWorker())
      return
    }
    const source = await readFile(file, 'utf8')
    const versionedSource = source.replace(
      /^(const version = ['"])([^'"]+)(['"])$/m,
      `$1$2-${fixtureVersion}$3`,
    )
    response.end(`${versionedSource}\n// pwa-fixture-version:${fixtureVersion}\n`)
  } else createReadStream(file).pipe(response)
  } catch (error) { console.error(error); if (!response.headersSent) send(response, { status: 500, json: { detail: 'fixture_error' } }); else response.destroy() }
})

function startOrigin() {
  return server.listening ? Promise.resolve() : new Promise(resolve => server.listen(port, '127.0.0.1', resolve))
}
function stopOrigin() {
  if (!server.listening) return Promise.resolve()
  return new Promise(resolve => { server.close(resolve); server.closeAllConnections() })
}
// Out-of-band control remains reachable while the actual application origin is
// stopped. This tests WebKit offline navigation without setOffline's upstream bug.
const control = createServer(async (request, response) => {
  try {
    const data = await readJson(request)
    if (request.url === '/scenario') {
      scenarioUser = data.role ? userForRole(data.role) : null
      signedIn = Boolean(data.signedIn); holdAuth = false
      routes = scenarioUser ? operationalRoutes({ user: scenarioUser }) : []
      for (const pending of pendingAuth) send(pending, { status: 401, json: { detail: 'not_authenticated' } })
      pendingAuth.clear()
    } else if (request.url === '/transport') {
      if (data.offline) await stopOrigin(); else await startOrigin()
    } else if (request.url === '/hold-auth') {
      holdAuth = Boolean(data.hold)
      if (!holdAuth) { for (const pending of pendingAuth) send(pending, { status: 401, json: { detail: 'not_authenticated' } }); pendingAuth.clear() }
    } else { response.writeHead(404).end(); return }
    response.writeHead(204).end()
  } catch (error) { console.error(error); response.writeHead(500).end() }
})
await startOrigin()
control.listen(port + 1, '127.0.0.1')
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => {
  server.closeAllConnections(); control.closeAllConnections()
  server.close(); control.close(() => process.exit(0))
})
