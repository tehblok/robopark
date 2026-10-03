import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { chmod, mkdtemp, mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

const scriptsRoot = dirname(fileURLToPath(import.meta.url))
const installScript = join(scriptsRoot, 'install-dependencies.sh')

async function runInstaller(attempts) {
  const root = await mkdtemp(join(tmpdir(), 'robopark-npm-install-'))
  const bin = join(root, 'bin')
  const fixtures = join(root, 'fixtures')
  const scratch = join(root, 'tmp')
  const state = join(root, 'attempt-count')
  const npmLog = join(root, 'npm-args')
  const sleepLog = join(root, 'sleep-args')
  await Promise.all([mkdir(bin), mkdir(fixtures), mkdir(scratch)])
  await writeFile(join(bin, 'npm'), `#!/bin/sh
count=0
if [ -f "$FAKE_NPM_STATE" ]; then count=$(cat "$FAKE_NPM_STATE"); fi
count=$((count + 1))
printf '%s' "$count" > "$FAKE_NPM_STATE"
printf '%s\\n' "$*" >> "$FAKE_NPM_LOG"
fixture="$FAKE_NPM_FIXTURES/$count"
[ ! -f "$fixture/stdout" ] || cat "$fixture/stdout"
[ ! -f "$fixture/stderr" ] || cat "$fixture/stderr" >&2
exit "$(cat "$fixture/status")"
`)
  await writeFile(join(bin, 'sleep'), `#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_SLEEP_LOG"
`)
  await Promise.all([chmod(join(bin, 'npm'), 0o755), chmod(join(bin, 'sleep'), 0o755)])
  for (const [index, attempt] of attempts.entries()) {
    const fixture = join(fixtures, String(index + 1))
    await mkdir(fixture)
    await Promise.all([
      writeFile(join(fixture, 'stdout'), attempt.stdout ?? ''),
      writeFile(join(fixture, 'stderr'), attempt.stderr ?? ''),
      writeFile(join(fixture, 'status'), String(attempt.status)),
    ])
  }
  const result = spawnSync('/bin/sh', [installScript], {
    cwd: dirname(scriptsRoot),
    encoding: 'utf8',
    env: {
      ...process.env,
      PATH: `${bin}:${process.env.PATH}`,
      TMPDIR: scratch,
      FAKE_NPM_STATE: state,
      FAKE_NPM_LOG: npmLog,
      FAKE_NPM_FIXTURES: fixtures,
      FAKE_SLEEP_LOG: sleepLog,
    },
  })
  const output = {
    ...result,
    attempts: Number(await readFile(state, 'utf8').catch(() => '0')),
    npmArgs: await readFile(npmLog, 'utf8').catch(() => ''),
    sleeps: await readFile(sleepLog, 'utf8').catch(() => ''),
    scratchFiles: await readdir(scratch),
  }
  await rm(root, { recursive: true, force: true })
  return output
}

test('retries transient npm failures with bounded backoff and preserves all npm output', async () => {
  const result = await runInstaller([
    { status: 146, stdout: 'attempt one stdout\n', stderr: 'npm error code ETIMEDOUT\nattempt one stderr\n' },
    { status: 72, stdout: 'attempt two stdout\n', stderr: 'npm error code EAI_AGAIN\nattempt two stderr\n' },
    { status: 0, stdout: 'installed dependencies\n' },
  ])
  assert.equal(result.status, 0, result.stderr)
  assert.equal(result.attempts, 3)
  assert.equal(result.sleeps, '10\n20\n')
  assert.equal(result.npmArgs, 'ci --prefer-offline --no-audit --no-fund\n'.repeat(3))
  assert.match(result.stdout, /attempt one stdout/)
  assert.match(result.stdout, /attempt two stdout/)
  assert.match(result.stdout, /installed dependencies/)
  assert.doesNotMatch(result.stdout, /attempt (one|two) stderr/)
  assert.match(result.stderr, /attempt one stderr/)
  assert.match(result.stderr, /attempt two stderr/)
  assert.deepEqual(result.scratchFiles, [])
})

test('stops after three transient failures and returns the final npm status', async () => {
  const result = await runInstaller([
    { status: 71, stderr: 'npm error code ECONNRESET\n' },
    { status: 72, stderr: 'npm error code ERR_SOCKET_TIMEOUT\n' },
    { status: 73, stderr: 'npm error code E503\n' },
  ])
  assert.equal(result.status, 73)
  assert.equal(result.attempts, 3)
  assert.equal(result.sleeps, '10\n20\n')
  assert.equal(result.npmArgs, 'ci --prefer-offline --no-audit --no-fund\n'.repeat(3))
})

test('does not retry when transient and unknown structured npm codes conflict', async () => {
  const result = await runInstaller([
    { status: 42, stderr: 'npm error code ETIMEDOUT\nnpm error code 42\n' },
    { status: 0, stdout: 'must not run\n' },
  ])
  assert.equal(result.status, 42)
  assert.equal(result.attempts, 1)
  assert.equal(result.sleeps, '')
  assert.equal(result.npmArgs, 'ci --prefer-offline --no-audit --no-fund\n')
  assert.match(result.stderr, /npm error code ETIMEDOUT/)
  assert.match(result.stderr, /npm error code 42/)
  assert.doesNotMatch(result.stdout, /must not run/)
  assert.deepEqual(result.scratchFiles, [])
})

test('does not retry permanent or unstructured npm failures', async (context) => {
  for (const failure of [
    { name: 'lock mismatch', status: 31, stderr: 'npm error code EUSAGE\nnpm error npm ci requires package-lock.json to match\n' },
    { name: 'integrity', status: 32, stderr: 'npm error code EINTEGRITY\n' },
    { name: 'authentication', status: 33, stderr: 'npm error code E401\n' },
    { name: 'unstructured network text', status: 34, stderr: 'network connection timed out\n' },
  ]) {
    await context.test(failure.name, async () => {
      const result = await runInstaller([failure])
      assert.equal(result.status, failure.status)
      assert.equal(result.attempts, 1)
      assert.equal(result.sleeps, '')
      assert.equal(result.npmArgs, 'ci --prefer-offline --no-audit --no-fund\n')
      assert.match(result.stderr, new RegExp(failure.stderr.split('\n')[0]))
      assert.deepEqual(result.scratchFiles, [])
    })
  }
})

test('returns immediately after a successful npm install', async () => {
  const result = await runInstaller([{ status: 0, stdout: 'complete\n' }])
  assert.equal(result.status, 0, result.stderr)
  assert.equal(result.attempts, 1)
  assert.equal(result.sleeps, '')
  assert.equal(result.npmArgs, 'ci --prefer-offline --no-audit --no-fund\n')
  assert.equal(result.stdout, 'complete\n')
  assert.deepEqual(result.scratchFiles, [])
})
