import { mkdir } from 'node:fs/promises'
import { resolve } from 'node:path'
import { expect, test } from '@playwright/test'

const revision = 'a'.repeat(64)

test('royal opens separate ordinary/root terminals and sees safe connection states', async ({ page }, testInfo) => {
  const auditDirectory = testInfo.outputPath('royal-terminal', 'web')
  await mkdir(auditDirectory, { recursive: true })
  const sessions = new Map<string, Record<string, unknown>>()
  let invalidTotp = true

  await page.addInitScript(() => {
    const sockets: Array<{ disconnect: () => void }> = []
    class TerminalSocket extends EventTarget {
      static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3
      readyState = 0
      binaryType = 'arraybuffer'
      onopen: ((event: Event) => void) | null = null
      onmessage: ((event: MessageEvent) => void) | null = null
      onclose: ((event: CloseEvent) => void) | null = null
      onerror: ((event: Event) => void) | null = null
      readonly id: string
      constructor(readonly url: string) {
        super()
        this.id = url.split('/').at(-2) ?? ''
        sockets.push(this)
        setTimeout(() => { this.readyState = 1; this.onopen?.(new Event('open')) }, 0)
      }
      send(data: string | ArrayBuffer) {
        if (typeof data !== 'string') return
        const message = JSON.parse(data)
        if (message.op !== 'authenticate') return
        if ((window as unknown as { __terminalFailReconnect?: boolean }).__terminalFailReconnect) {
          setTimeout(() => this.disconnect(), 0); return
        }
        const profiles = (window as unknown as { __terminalProfiles?: Record<string, string> }).__terminalProfiles ?? {}
        const session = { id: this.id, profile: profiles[this.id] ?? 'maintenance', state: 'active', expires_at: '2026-10-02T14:30:00Z', broker_epoch: 'epoch-e2e', termination_reason: null }
        setTimeout(() => {
          this.onmessage?.(new MessageEvent('message', { data: JSON.stringify({ op: 'ready', session }) }))
          const output = new TextEncoder().encode('\u001b]52;c;Zm9yYmlkZGVu\u0007\u001b]8;;https://evil.invalid\u0007safe output\u001b]8;;\u0007\r\n')
          this.onmessage?.(new MessageEvent('message', { data: output.buffer }))
        }, 0)
      }
      close() { this.disconnect() }
      disconnect() {
        if (this.readyState === 3) return
        this.readyState = 3
        this.onclose?.(new CloseEvent('close'))
      }
    }
    Object.defineProperty(window, 'WebSocket', { value: TerminalSocket })
    Object.assign(TerminalSocket, { instances: sockets })
    ;(window as unknown as { __disconnectTerminal?: () => void }).__disconnectTerminal = () => sockets.at(-1)?.disconnect()
  })

  await page.route('**/api/**', async route => {
    const request = route.request()
    const url = new URL(request.url())
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) })
    if (url.pathname === '/api/auth/me') return json({ id: 7, username: 'owner', role: 'royal', access_status: 'approved' })
    if (url.pathname === '/api/admin/terminal/capabilities') return json({ available: true, boot_id: 'boot-e2e', broker_epoch: 'epoch-e2e', capability_revision: revision, profiles: ['maintenance', 'root'], active_sessions: sessions.size })
    if (url.pathname === '/api/admin/terminal/sessions' && request.method() === 'GET') return json([...sessions.values()])
    if (url.pathname === '/api/admin/privileged-auth/reauthorize') {
      if (invalidTotp) { invalidTotp = false; return json({ detail: 'invalid_totp' }, 401) }
      return json({ token: 'memory-only-grant', expires_in: 30 })
    }
    if (url.pathname === '/api/admin/terminal/sessions' && request.method() === 'POST') {
      const body = request.postDataJSON() as { id: string; profile: string }
      const session = { id: body.id, profile: body.profile, state: 'active', expires_at: '2026-10-02T14:30:00Z', broker_epoch: 'epoch-e2e', termination_reason: null }
      sessions.set(body.id, session)
      await page.evaluate(({ id, profile }) => {
        const target = window as unknown as { __terminalProfiles?: Record<string, string> }
        target.__terminalProfiles ??= {}; target.__terminalProfiles[id] = profile
      }, body)
      return json(session)
    }
    if (url.pathname.endsWith('/attach-ticket')) return json({ ticket: '12345678901234567890-ticket', expires_in: 15 })
    if (request.method() === 'DELETE') {
      const id = url.pathname.split('/').at(-1) ?? ''
      const session = { ...sessions.get(id), state: 'ended', termination_reason: 'closed' }
      sessions.set(id, session); return json(session)
    }
    return json({ detail: 'not_found' }, 404)
  })

  await page.goto('/terminal.html')
  await expect(page.getByRole('heading', { name: 'Терминал хоста' })).toBeVisible()

  const authorize = async (button: 'Открыть терминал' | 'Открыть root') => {
    await page.getByRole('button', { name: button }).click()
    await page.getByLabel('Пароль').fill('e2e-password')
    await page.getByLabel('Свежий код TOTP').fill('123456')
    await page.getByRole('button', { name: 'Подтвердить и открыть' }).click()
  }

  await authorize('Открыть терминал')
  await expect(page.getByText('Неверный пароль или код TOTP. Вход на сайт сохранён.')).toBeVisible()
  await page.screenshot({ path: resolve(auditDirectory, 'error.png'), fullPage: true })
  await page.getByLabel('Пароль').fill('e2e-password')
  await page.getByLabel('Свежий код TOTP').fill('234567')
  await page.getByRole('button', { name: 'Подтвердить и открыть' }).click()
  await expect(page.getByRole('region', { name: 'Обычный терминал' })).toBeVisible()
  await expect(page.getByRole('log')).toContainText('safe output')
  await expect(page.locator('a[href="https://evil.invalid"]')).toHaveCount(0)
  await page.screenshot({ path: resolve(auditDirectory, 'normal.png'), fullPage: true })

  await authorize('Открыть root')
  await expect(page.getByRole('region', { name: 'Root терминал' })).toBeVisible()
  await expect(page.getByText(/root · завершится/)).toBeVisible()
  await page.screenshot({ path: resolve(auditDirectory, 'root.png'), fullPage: true })

  await page.evaluate(() => {
    ;(window as unknown as { __terminalFailReconnect?: boolean }).__terminalFailReconnect = true
    ;(window as unknown as { __disconnectTerminal?: () => void }).__disconnectTerminal?.()
  })
  await expect(page.getByText('detached', { exact: true })).toBeVisible()
  await page.screenshot({ path: resolve(auditDirectory, 'disconnected.png'), fullPage: true })
})
