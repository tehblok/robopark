// @vitest-environment node
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { setTimeout as watchdog, clearTimeout as clearWatchdog } from 'node:timers'
import { afterEach, expect, it, vi } from 'vitest'
import { startDiagnosticApi } from './diagnosticApi'

vi.mock('node:child_process', async importOriginal => {
  const original = await importOriginal<typeof import('node:child_process')>()
  return { ...original, spawn: vi.fn(original.spawn) }
})
const { spawn: realSpawn } = await vi.importActual<typeof import('node:child_process')>('node:child_process')

const children: { child: ChildProcessWithoutNullStreams; exited: Promise<void> }[] = []

async function within<T>(promise: Promise<T>, milliseconds = 2500): Promise<T> {
  let timer: ReturnType<typeof watchdog> | undefined
  try {
    return await Promise.race([
      promise,
      new Promise<never>((_, reject) => {
        timer = watchdog(() => reject(new Error('Bridge shutdown exceeded its deadline')), milliseconds)
      }),
    ])
  } finally {
    clearWatchdog(timer)
  }
}

async function startFixture(unresponsive = false) {
  // Substitute only the launched executable. The transport and child lifecycle
  // are real; this process implements the same ready/request JSON-lines protocol.
  vi.mocked(spawn).mockImplementationOnce((_command, _args, options) => {
    const child = realSpawn(process.execPath, ['-e', `
      const lines = require('node:readline').createInterface({ input: process.stdin });
      if (${unresponsive}) {
        process.on('SIGTERM', () => {});
        setInterval(() => {}, 1000);
      }
      lines.on('line', line => {
        const request = JSON.parse(line);
        if (request.path === '/hang') return;
        if (request.path === '/exit') { process.exit(0); return; }
        console.log(JSON.stringify({ id: request.id, status: 200, body: 'alive' }));
      });
      console.log(JSON.stringify({ ready: true }));
    `], options) as ChildProcessWithoutNullStreams
    const exited = new Promise<void>(resolve => child.once('exit', () => resolve()))
    children.push({ child, exited })
    return child
  })
  const api = await startDiagnosticApi()
  return { api, ...children.at(-1)! }
}

afterEach(async () => {
  vi.useRealTimers()
  for (const { child, exited } of children.splice(0)) {
    if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL')
    await within(exited)
    child.stdin.destroy(); child.stdout.destroy(); child.stderr.destroy()
    child.removeAllListeners()
  }
  vi.restoreAllMocks()
})

function expectReleased(child: ChildProcessWithoutNullStreams) {
  expect(child.listenerCount('exit')).toBe(0)
  expect(child.listenerCount('error')).toBe(0)
  expect(child.stderr.listenerCount('data')).toBe(0)
  expect(child.stdout.listenerCount('data')).toBe(0)
  expect(child.stdin.destroyed).toBe(true)
  expect(child.stdout.destroyed).toBe(true)
  expect(child.stderr.destroyed).toBe(true)
  expect(() => process.kill(child.pid!, 0)).toThrow(/ESRCH/)
}

it('ends stdin for normal shutdown and releases child resources', async () => {
  const { api, child } = await startFixture()
  expect(await api.call({ method: 'GET', path: '/health' })).toMatchObject({ status: 200, body: 'alive' })
  await within(api.close())
  expect(child.exitCode).toBe(0)
  expectReleased(child)
})

it('closes a child that already exited normally', async () => {
  const { api, child, exited } = await startFixture()
  await expect(api.call({ method: 'GET', path: '/exit' })).rejects.toThrow('bridge exited')
  await exited
  await within(api.close())
  expect(child.exitCode).toBe(0)
  expectReleased(child)
})

it('closes a child already terminated by SIGTERM and rejects in-flight requests', async () => {
  const { api, child, exited } = await startFixture()
  const rejected = expect(api.call({ method: 'GET', path: '/hang' })).rejects.toThrow('bridge exited')
  child.kill('SIGTERM')
  await exited
  await rejected
  expect(child.exitCode).toBeNull()
  expect(child.signalCode).toBe('SIGTERM')
  await within(api.close())
  expectReleased(child)
})

it('force terminates an unresponsive child within a bounded shutdown', async () => {
  const { api, child } = await startFixture(true)
  const pending = api.call({ method: 'GET', path: '/hang' }).catch(error => error)
  await within(api.close())
  expect(await pending).toMatchObject({ message: expect.stringMatching(/bridge (exited|closed)/) })
  expect(child.signalCode).toBe('SIGKILL')
  expectReleased(child)
})

it('rejects requests after close instead of waiting for a stopped bridge', async () => {
  const { api } = await startFixture()
  await within(api.close())
  await expect(within(api.call({ method: 'GET', path: '/health' }))).rejects.toThrow('bridge closed')
})

it('shares concurrent and repeated close completion and clears graceful timers', async () => {
  const { api, child } = await startFixture()
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const closing = api.close()
  const again = api.close()
  await within(Promise.all([closing, again]))
  expect(again).toBe(closing)
  expect(api.close()).toBe(closing)
  expect(vi.getTimerCount()).toBe(0)
  expectReleased(child)
})
