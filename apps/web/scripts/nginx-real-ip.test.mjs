import assert from 'node:assert/strict'
import { execFileSync, spawnSync } from 'node:child_process'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const nginxImage = 'nginx:1.30.5-alpine@sha256:0985e772fb9f729e6fa0980da05fca5d9c468e870eed43071545afa9d2e27d94'

function docker(...args) {
  return execFileSync('docker', args, { encoding: 'utf8' }).trim()
}

function loggedAddress(logs, path) {
  const line = logs.split('\n').find(candidate => candidate.includes(`GET ${path} `))
  assert.ok(line, `nginx access log does not contain ${path}:\n${logs}`)
  return line.split(' ', 1)[0]
}

async function request(url, headers = {}) {
  let lastError
  for (let attempt = 0; attempt < 20; attempt += 1) {
    try {
      const response = await fetch(url, { headers })
      await response.arrayBuffer()
      return response.status
    } catch (error) {
      lastError = error
      await new Promise(resolveWait => setTimeout(resolveWait, 100))
    }
  }
  throw lastError
}

test('trusted Tuna hops select the external client while an untrusted container cannot spoof it', async context => {
  const available = spawnSync('docker', ['info'], { encoding: 'utf8' })
  if (available.status !== 0) {
    context.skip(`Docker daemon unavailable: ${available.stderr.trim()}`)
    return
  }

  const suffix = `${process.pid}-${Date.now()}`
  const network = `robopark-real-ip-${suffix}`
  const proxy = `robopark-real-ip-proxy-${suffix}`
  const sibling = `robopark-real-ip-sibling-${suffix}`
  const scratchParent = join(webRoot, 'tmp')
  await mkdir(scratchParent, { recursive: true })
  const scratch = await mkdtemp(join(scratchParent, 'nginx-real-ip-'))
  const realIpDir = join(scratch, 'realip.d')
  await mkdir(realIpDir)

  try {
    docker('network', 'create', network)
    const gateway = docker('network', 'inspect', network, '--format', '{{(index .IPAM.Config 0).Gateway}}')
    await writeFile(join(realIpDir, 'host.conf'), `set_real_ip_from ${gateway};\n`)

    docker(
      'run', '-d', '--name', sibling, '--network', network, '--network-alias', 'api',
      '--entrypoint', 'sleep', nginxImage, '60',
    )
    const siblingAddress = docker(
      'inspect', sibling, '--format', `{{with index .NetworkSettings.Networks "${network}"}}{{.IPAddress}}{{end}}`,
    )
    docker(
      'run', '-d', '--name', proxy, '--network', network, '-p', '127.0.0.1::80',
      '-v', `${join(webRoot, 'nginx.conf')}:/etc/nginx/conf.d/default.conf:ro`,
      '-v', `${realIpDir}:/etc/nginx/realip.d:ro`,
      '--entrypoint', 'nginx', nginxImage, '-g', 'daemon off;',
    )
    const published = docker('port', proxy, '80/tcp').split('\n')[0]
    const publicPath = '/audit-tuna-real-ip'
    const status = await request(`http://${published}${publicPath}`, {
      'X-Forwarded-For': '203.0.113.42, 127.0.0.1, 127.0.0.1',
    })
    assert.equal(status, 200)

    const siblingPath = '/audit-untrusted-sibling'
    docker(
      'exec', sibling, 'wget', '-qO-',
      '--header=X-Forwarded-For: 198.51.100.77, 127.0.0.1',
      `http://${proxy}${siblingPath}`,
    )

    const logs = docker('logs', proxy)
    const evidence = {
      trusted_gateway: gateway,
      tuna_chain: '203.0.113.42, 127.0.0.1, 127.0.0.1',
      tuna_remote_addr: loggedAddress(logs, publicPath),
      untrusted_peer: siblingAddress,
      untrusted_spoof: '198.51.100.77, 127.0.0.1',
      untrusted_remote_addr: loggedAddress(logs, siblingPath),
    }
    assert.equal(evidence.tuna_remote_addr, '203.0.113.42')
    assert.equal(evidence.untrusted_remote_addr, siblingAddress)

    if (process.env.ROBOPARK_NGINX_AUDIT_DIR) {
      const auditDir = resolve(process.env.ROBOPARK_NGINX_AUDIT_DIR)
      await mkdir(auditDir, { recursive: true })
      await writeFile(join(auditDir, 'local-nginx-real-ip.json'), `${JSON.stringify(evidence, null, 2)}\n`)
      await writeFile(join(auditDir, 'nginx-access.log'), `${logs}\n`)
      await writeFile(join(auditDir, 'nginx-config.txt'), await readFile(join(webRoot, 'nginx.conf')))
    }
  } finally {
    spawnSync('docker', ['rm', '-f', sibling, proxy], { encoding: 'utf8' })
    spawnSync('docker', ['network', 'rm', network], { encoding: 'utf8' })
    await rm(scratch, { recursive: true, force: true })
  }
})
