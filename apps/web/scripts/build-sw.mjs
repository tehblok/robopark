import { createHash } from 'node:crypto'
import { readFile, readdir, writeFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptDirectory = dirname(fileURLToPath(import.meta.url))
const staticFiles = ['/index.html', '/offline.html', '/pwa-icon-192.png', '/pwa-icon-512.png']

async function routeAssets(dist, prefix = '/assets') {
  const entries = await readdir(join(dist, prefix.slice(1)), { withFileTypes: true })
  const paths = await Promise.all(entries.map(entry => {
    const path = `${prefix}/${entry.name}`
    if (prefix === '/assets' && entry.isDirectory() && entry.name === 'terminal') return []
    if (entry.isDirectory()) return routeAssets(dist, path)
    return entry.isFile() && /^[A-Za-z0-9_-]+\.(?:js|css)$/.test(entry.name) ? [path] : []
  }))
  return paths.flat().sort()
}

export async function buildServiceWorker(distDirectory) {
  const dist = resolve(distDirectory)
  const index = await readFile(join(dist, 'index.html'))
  const assets = [...index.toString().matchAll(/(?:src|href)="(\/assets\/[A-Za-z0-9_/-]+\.(?:js|css))"/g)]
    .map((match) => match[1])
  if (!assets.some((asset) => asset.endsWith('.js')) || !assets.some((asset) => asset.endsWith('.css'))) {
    throw new Error('Built index.html needs entry JavaScript and CSS')
  }
  // The first route can load before this worker controls its client. Runtime
  // caching alone therefore cannot guarantee an offline restart of that route.
  const precache = [...new Set([...staticFiles, ...assets, ...await routeAssets(dist)])]
  const digest = createHash('sha256').update(index)
  for (const path of precache) {
    // Only root-relative known build paths reach this point; no arbitrary file traversal.
    digest.update(path).update(await readFile(join(dist, path.slice(1))))
  }
  const template = await readFile(join(scriptDirectory, 'sw-template.js'), 'utf8')
  const { version } = JSON.parse(await readFile(join(scriptDirectory, '..', 'package.json'), 'utf8'))
  const source = template
    .replace("'__CACHE_VERSION__'", JSON.stringify(`${version}-${digest.digest('hex').slice(0, 16)}`))
    .replace("['__PRECACHE__']", JSON.stringify(precache))
  await writeFile(join(dist, 'sw.js'), source)
  return source
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await buildServiceWorker(join(scriptDirectory, '..', 'dist'))
}
