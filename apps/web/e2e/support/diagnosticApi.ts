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
  let sequence = 0, stderr = ''
  const pending = new Map<number, { resolve: (value: MockResponse) => void; reject: (reason: Error) => void }>()
  let ready!: () => void, failed!: (reason: Error) => void
  const started = new Promise<void>((resolve, reject) => { ready = resolve; failed = reject })
  const lines = createInterface({ input: child.stdout })
  lines.on('line', line => {
    const message = JSON.parse(line)
    if (message.ready) ready()
    else { pending.get(message.id)?.resolve(message); pending.delete(message.id) }
  })
  child.stderr.on('data', chunk => { stderr += chunk.toString() })
  const fail = (error: Error) => { failed(error); for (const call of pending.values()) call.reject(error); pending.clear() }
  child.on('error', fail)
  child.on('exit', code => fail(new Error(`Diagnostic API bridge exited ${code}: ${stderr}`)))
  await started
  const call = (input: Call, as: Actor = actor) => new Promise<MockResponse>((resolve, reject) => {
    const id = ++sequence; pending.set(id, { resolve, reject }); child.stdin.write(`${JSON.stringify({ id, actor: as, ...input, headers: { 'content-type': 'application/json', ...input.headers } })}\n`)
  })
  const forward = async (request: Request) => call({ method: request.method, path: new URL(request.url).pathname.replace(/^\/api/, '') + new URL(request.url).search, body: ['GET', 'HEAD'].includes(request.method) ? undefined : await request.text(), headers: { 'content-type': 'application/json', ...(request.headers.has('if-match') ? { 'if-match': request.headers.get('if-match')! } : {}) } })
  const routes: MockRoute[] = (['GET', 'POST', 'PATCH', 'PUT', 'DELETE'] as const).map(method => ({ method, path: /^\/api\/(?:admin\/diagnostic-rules(?:\/.*)?|auth\/me|emergency\/(?:resolve|[^/]+\/snapshot))$/, handler: forward }))
  return { call, routes, close: async () => {
    lines.close(); child.stdin.end()
    if (child.exitCode === null) await new Promise<void>(resolve => { child.once('exit', () => resolve()) })
  } }
}
