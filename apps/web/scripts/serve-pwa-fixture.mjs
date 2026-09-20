import { createReadStream } from 'node:fs'
import { readFile, stat } from 'node:fs/promises'
import { createServer } from 'node:http'
import { extname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const port = Number(process.argv[2])
if (!Number.isInteger(port) || port < 1 || port > 65_535) throw new Error('A valid fixture port is required')

const dist = resolve(fileURLToPath(new URL('../dist', import.meta.url)))
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

const server = createServer(async (request, response) => {
  const url = new URL(request.url ?? '/', 'http://127.0.0.1')
  if (request.method === 'POST' && url.pathname === '/__pwa_fixture__/version') {
    fixtureVersion = 'v2'
    response.writeHead(204, { 'cache-control': 'no-store' }).end()
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

  const file = await resolvePublicFile(decodeURIComponent(url.pathname))
  if (!file) {
    response.writeHead(403).end()
    return
  }
  response.writeHead(200, {
    'cache-control': 'no-cache',
    'content-type': contentTypes.get(extname(file)) ?? 'application/octet-stream',
  })
  if (request.method === 'HEAD') response.end()
  else if (file === join(dist, 'index.html')) {
    const source = await readFile(file, 'utf8')
    response.end(source.replace('</head>', `<meta name="pwa-fixture-version" content="${fixtureVersion}"></head>`))
  } else if (file === join(dist, 'sw.js')) {
    const source = await readFile(file, 'utf8')
    const versionedSource = source.replace(
      /^(const version = ['"])([^'"]+)(['"])$/m,
      `$1$2-${fixtureVersion}$3`,
    )
    response.end(`${versionedSource}\n// pwa-fixture-version:${fixtureVersion}\n`)
  } else createReadStream(file).pipe(response)
})

server.listen(port, '127.0.0.1')
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => server.close(() => process.exit(0)))
