import { createHash } from 'node:crypto'
import { readFile, writeFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptDirectory = dirname(fileURLToPath(import.meta.url))
const staticFiles = ['/offline.html', '/pwa-icon-192.png', '/pwa-icon-512.png']

export async function buildServiceWorker(distDirectory) {
  const dist = resolve(distDirectory)
  const index = await readFile(join(dist, 'index.html'))
  const assets = [...index.toString().matchAll(/(?:src|href)="(\/assets\/[A-Za-z0-9_/-]+\.(?:js|css))"/g)]
    .map((match) => match[1])
  if (!assets.some((asset) => asset.endsWith('.js')) || !assets.some((asset) => asset.endsWith('.css'))) {
    throw new Error('Built index.html needs entry JavaScript and CSS')
  }
  const precache = [...new Set([...staticFiles, ...assets])]
  const digest = createHash('sha256').update(index)
  for (const path of precache) {
    // Only root-relative known build paths reach this point; no arbitrary file traversal.
    digest.update(path).update(await readFile(join(dist, path.slice(1))))
  }
  const template = await readFile(join(scriptDirectory, 'sw-template.js'), 'utf8')
  const source = template
    .replace("'__CACHE_VERSION__'", JSON.stringify(digest.digest('hex').slice(0, 16)))
    .replace("['__PRECACHE__']", JSON.stringify(precache))
  await writeFile(join(dist, 'sw.js'), source)
  return source
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await buildServiceWorker(join(scriptDirectory, '..', 'dist'))
}
