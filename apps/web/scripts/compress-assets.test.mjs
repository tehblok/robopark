import assert from 'node:assert/strict'
import { execFileSync, spawnSync } from 'node:child_process'
import { chmod, mkdtemp, mkdir, readFile, rm, stat, symlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { get } from 'node:http'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { gunzipSync } from 'node:zlib'
import { compressAssets } from './compress-assets.mjs'

const webRoot = join(dirname(fileURLToPath(import.meta.url)), '..')

test('precompresses useful static assets deterministically without modifying source bytes', async () => {
  const dist = await mkdtemp(join(tmpdir(), 'robopark-gzip-'))
  try {
    await mkdir(join(dist, 'assets', 'terminal'), { recursive: true })
    const source = 'export const useful = "repeated build contents";\n'.repeat(100)
    const file = join(dist, 'assets', 'terminal', 'index-abc.js')
    await writeFile(file, source)
    await compressAssets(dist)
    const encoded = await readFile(`${file}.gz`)
    assert.equal(gunzipSync(encoded).toString(), source)
    assert.ok(encoded.length < Buffer.byteLength(source))
    assert.equal(await readFile(file, 'utf8'), source)
    assert.ok(Math.abs((await stat(file)).mtimeMs - (await stat(`${file}.gz`)).mtimeMs) < 1)
    await compressAssets(dist)
    assert.deepEqual(await readFile(`${file}.gz`), encoded)
  } finally { await rm(dist, { recursive: true, force: true }) }
})

test('nginx serves prebuilt gzip bytes with negotiated identity fallback and unchanged HTML policy', async context => {
  if (spawnSync('docker', ['info'], { encoding: 'utf8' }).status !== 0) {
    context.skip('Docker daemon unavailable')
    return
  }
  const docker = (...args) => execFileSync('docker', args, { encoding: 'utf8', timeout: 60000 }).trim()
  await mkdir(join(webRoot, 'tmp'), { recursive: true })
  const dist = await mkdtemp(join(webRoot, 'tmp', 'gzip-http-'))
  let container
  const request = (url, encoding) => new Promise((resolve, reject) => {
    get(url, { headers: { 'Accept-Encoding': encoding }, timeout: 2000 }, response => {
      const parts = []
      response.on('data', part => parts.push(part))
      response.on('end', () => resolve({ status: response.statusCode, headers: response.headers, body: Buffer.concat(parts) }))
      response.on('error', reject)
    }).on('error', reject).on('timeout', function () { this.destroy(new Error('HTTP timeout')) })
  })
  try {
    // mkdtemp uses 0700; the unprivileged nginx worker must traverse this mount
    // on native Linux too (some desktop Docker filesystems mask the problem).
    await chmod(dist, 0o755)
    await mkdir(join(dist, 'assets'))
    const source = 'const immutable = "static gzip negotiation";\n'.repeat(100)
    await writeFile(join(dist, 'assets', 'app-a1.js'), source)
    await writeFile(join(dist, 'index.html'), '<!doctype html><title>Robopark test</title>')
    await compressAssets(dist)
    container = docker('run', '-d', '-p', '127.0.0.1::80',
      '-v', `${dist}:/usr/share/nginx/html:ro`,
      '-v', `${join(webRoot, 'nginx.conf')}:/etc/nginx/conf.d/default.conf:ro`,
      '--entrypoint', 'nginx',
      'nginx:1.30.5-alpine@sha256:0985e772fb9f729e6fa0980da05fca5d9c468e870eed43071545afa9d2e27d94', '-g', 'daemon off;')
    const base = `http://${docker('port', container, '80/tcp').split('\n')[0]}`
    let zipped
    for (let attempt = 0; attempt < 30; attempt++) {
      try { zipped = await request(`${base}/assets/app-a1.js`, 'gzip'); break }
      catch (error) { if (attempt === 29) throw error; await new Promise(resolve => setTimeout(resolve, 100)) }
    }
    assert.equal(zipped.status, 200)
    assert.equal(zipped.headers['content-encoding'], 'gzip')
    assert.match(zipped.headers.vary, /Accept-Encoding/i)
    assert.deepEqual(zipped.body, await readFile(join(dist, 'assets', 'app-a1.js.gz')))
    assert.match(zipped.headers['cache-control'], /immutable/)
    const identity = await request(`${base}/assets/app-a1.js`, 'identity')
    assert.equal(identity.headers['content-encoding'], undefined)
    assert.equal(identity.body.toString(), source)
    const html = await request(`${base}/index.html`, 'gzip')
    assert.match(html.headers['cache-control'], /no-store/)
    const missing = await request(`${base}/assets/missing.js`, 'gzip')
    assert.equal(missing.status, 404)
  } finally {
    if (container) docker('rm', '--force', container)
    await rm(dist, { recursive: true, force: true })
  }
})

test('skips HTML, maps, compressed media and symlinks; removes stale gzip for small files', async () => {
  const dist = await mkdtemp(join(tmpdir(), 'robopark-gzip-'))
  try {
    await mkdir(join(dist, 'assets'))
    for (const file of ['index.html', 'sw.js', 'assets/source.js.map', 'assets/font.woff2', 'assets/photo.png']) {
      await writeFile(join(dist, file), 'unchanged'.repeat(1000))
    }
    await writeFile(join(dist, 'assets', 'tiny.css'), 'body{}')
    await writeFile(join(dist, 'assets', 'tiny.css.gz'), 'old stale bytes')
    await symlink(join(dist, 'sw.js'), join(dist, 'assets', 'linked.js'))
    await compressAssets(dist)
    for (const file of ['index.html', 'sw.js', 'assets/source.js.map', 'assets/font.woff2', 'assets/photo.png', 'assets/tiny.css', 'assets/linked.js']) {
      await assert.rejects(stat(join(dist, `${file}.gz`)), { code: 'ENOENT' })
    }
  } finally { await rm(dist, { recursive: true, force: true }) }
})
