import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import { fileURLToPath } from 'node:url'
import type { MockResponse, MockRoute } from './mockApi'

type Actor = 'admin' | 'royal' | 'operator' | 'custom-admin'
type Call = { method?: string; path?: string; body?: string; headers?: Record<string, string>; audit?: boolean }

/** Persistent TestClient + temporary SQLite, using production routes/auth/matching. */
export async function startDiagnosticApi(actor: Actor = 'admin') {
  const apiRoot = fileURLToPath(new URL('../../../api/', import.meta.url))
  const python = process.env.DIAGNOSTIC_E2E_PYTHON ?? `${apiRoot}.venv/bin/python`
  const child = spawn(python, ['-u', 'tests/browser_diagnostic_bridge.py'], {
    cwd: apiRoot, stdio: ['pipe', 'pipe', 'pipe'],
    // Fixture configuration cannot inherit an operator's integration credentials.
    env: { PATH: process.env.PATH, PYTHONDONTWRITEBYTECODE: '1', DIAGNOSTIC_E2E_MUTATION: process.env.DIAGNOSTIC_E2E_MUTATION },
  })
  // Subscribe at spawn time: exitCode remains null for a signal-terminated child.
  let resolveExit!: () => void
  const exited = new Promise<void>(resolve => { resolveExit = resolve })
  child.once('exit', resolveExit)
  let closing: Promise<void> | undefined
  let sequence = 0, stderr = ''
  const pending = new Map<number, { resolve: (value: MockResponse) => void; reject: (reason: Error) => void }>()
  let ready!: () => void, failed!: (reason: Error) => void
  const started = new Promise<void>((resolve, reject) => { ready = resolve; failed = reject })
  const lines = createInterface({ input: child.stdout })
  const onLine = (line: string) => {
    const message = JSON.parse(line)
    if (message.ready) ready()
    else { pending.get(message.id)?.resolve(message); pending.delete(message.id) }
  }
  lines.on('line', onLine)
  const onStderr = (chunk: Buffer) => { stderr += chunk.toString() }
  child.stderr.on('data', onStderr)
  const fail = (error: Error) => { failed(error); for (const call of pending.values()) call.reject(error); pending.clear() }
  const onExit = (code: number | null, signal: NodeJS.Signals | null) => fail(new Error(`Diagnostic API bridge exited ${signal ?? code}: ${stderr}`))
  child.on('error', fail)
  child.once('exit', onExit)
  const waitForExit = async () => {
    let timer: ReturnType<typeof setTimeout> | undefined
    try {
      return await Promise.race([
        exited.then(() => true),
        new Promise<boolean>(resolve => { timer = setTimeout(() => resolve(false), 1000) }),
      ])
    } finally { clearTimeout(timer) }
  }
  const close = () => closing ??= (async () => {
    try {
      lines.close(); child.stdin.end()
      if (child.exitCode === null && child.signalCode === null && !await waitForExit()) {
        child.kill('SIGKILL')
        if (!await waitForExit()) throw new Error('Diagnostic API bridge did not exit after SIGKILL')
      }
      await exited
    } finally {
      fail(new Error('Diagnostic API bridge closed'))
      lines.off('line', onLine)
      child.off('exit', resolveExit); child.off('exit', onExit); child.off('error', fail)
      child.stderr.off('data', onStderr)
      child.stdin.destroy(); child.stdout.destroy(); child.stderr.destroy()
    }
  })()
  await started
  const call = (input: Call, as: Actor = actor) => new Promise<MockResponse>((resolve, reject) => {
    if (closing || child.exitCode !== null || child.signalCode !== null) {
      reject(new Error('Diagnostic API bridge closed')); return
    }
    const id = ++sequence; pending.set(id, { resolve, reject }); child.stdin.write(`${JSON.stringify({ id, actor: as, ...input, headers: { 'content-type': 'application/json', ...input.headers } })}\n`)
  })
  const forward = async (request: Request): Promise<MockResponse> => {
    try {
      return await call({ method: request.method, path: new URL(request.url).pathname.replace(/^\/api/, '') + new URL(request.url).search, body: ['GET', 'HEAD'].includes(request.method) ? undefined : await request.text(), headers: { 'content-type': 'application/json', ...(request.headers.has('if-match') ? { 'if-match': request.headers.get('if-match')! } : {}) } })
    } catch (error) {
      // Browser revalidation may still be in flight when test teardown closes
      // the bridge. Only deliberate shutdown gets an ordinary route response;
      // unexpected process exits and direct test API calls still fail loudly.
      if (closing) return { status: 503, json: { detail: 'diagnostic_api_closed' } }
      throw error
    }
  }
  const routes: MockRoute[] = (['GET', 'POST', 'PATCH', 'PUT', 'DELETE'] as const).map(method => ({ method, path: /^\/api\/(?:admin\/diagnostic-rules(?:\/.*)?|auth\/me|emergency\/(?:resolve|[^/]+\/snapshot))$/, handler: forward }))
  return { call, routes, close }
}
