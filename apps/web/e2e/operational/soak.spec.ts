import { test, expect } from '@playwright/test'
import { readdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { installOperational, issue, settlePage, userForRole } from './fixtures'

type SoakCounters = { timer_count: number; subscription_count: number; object_url_count: number; media_track_count: number }
type SoakSample = SoakCounters & {
  browser_rss_bytes: number; fd_count: number; cache_bytes: number; storage_bytes: number
  db_pool_checked_out: number; errors: number; operations: string[]; elapsed_seconds: number
  cycles: number; background_requests: number
}

function browserResources() {
  const processes = readdirSync('/proc').filter(name => /^\d+$/.test(name)).flatMap(pid => {
    try {
      const command = readFileSync(`/proc/${pid}/comm`, 'utf8').trim()
      const rss = /^VmRSS:\s+(\d+)\s+kB$/m.exec(readFileSync(`/proc/${pid}/status`, 'utf8'))
      return /(chromium|chrome|headless)/i.test(command) && rss ? [{ pid, rssBytes: Number(rss[1]) * 1024 }] : []
    } catch { return [] }
  })
  if (!processes.length) throw new Error('browser_process_metrics_unavailable')
  return {
    browser_rss_bytes: processes.reduce((total, process) => total + process.rssBytes, 0),
    fd_count: processes.reduce((total, process) => {
      try { return total + readdirSync(`/proc/${process.pid}/fd`).length } catch { return total }
    }, 0),
  }
}

function atomicWrite(path: string, value: object) {
  const temporary = `${path}.tmp`
  writeFileSync(temporary, `${JSON.stringify(value)}\n`)
  renameSync(temporary, path)
}

test('continuous UI soak samples one browser context and fails closed on real errors', async ({ page }) => {
  const durationInput = process.env.ROBOPARK_SOAK_DURATION_SECONDS
  const durationSeconds = Number(durationInput)
  const output = process.env.ROBOPARK_SOAK_OUTPUT
  const runToken = process.env.ROBOPARK_SOAK_RUN_TOKEN
  expect(durationInput).toBeTruthy()
  expect(Number.isFinite(durationSeconds) && durationSeconds > 0).toBe(true)
  expect(output).toBeTruthy()
  expect(runToken).toBeTruthy()
  test.setTimeout((durationSeconds + 90) * 1000)

  await page.addInitScript(() => {
    Object.defineProperty(window, '__roboparkSoakDocument', { value: crypto.randomUUID() })
    const counters: SoakCounters = { timer_count: 0, subscription_count: 0, object_url_count: 0, media_track_count: 0 }
    Object.defineProperty(window, '__roboparkSoak', { value: counters })
    const nativeSetInterval = window.setInterval.bind(window)
    const nativeClearInterval = window.clearInterval.bind(window)
    const nativeSetTimeout = window.setTimeout.bind(window)
    const timers = new Set<number>()
    window.setInterval = ((handler: TimerHandler, timeout?: number, ...args: unknown[]) => {
      const id = nativeSetInterval(handler, timeout, ...args); timers.add(id); counters.timer_count = timers.size; return id
    }) as typeof window.setInterval
    window.setTimeout = ((handler: TimerHandler, timeout?: number, ...args: unknown[]) => {
      if (typeof handler !== 'function') return nativeSetTimeout(handler, timeout, ...args)
      const id = nativeSetTimeout(() => { timers.delete(id); counters.timer_count = timers.size; handler.apply(window, args) }, timeout)
      timers.add(id); counters.timer_count = timers.size; return id
    }) as typeof window.setTimeout
    // Browsers use one ID pool and permit either clear function for either kind.
    window.clearTimeout = window.clearInterval = ((id?: number) => {
      if (id !== undefined) timers.delete(id)
      counters.timer_count = timers.size; return nativeClearInterval(id)
    }) as typeof window.clearInterval
    const nativeAdd = EventTarget.prototype.addEventListener
    const nativeRemove = EventTarget.prototype.removeEventListener
    // Track global subscriptions by callback identity and capture flag. DOM
    // node listeners die with their nodes and cannot be counted with a WeakMap
    // plus a monotonically incrementing counter.
    type Subscription = { wrapped: EventListenerOrEventListenerObject; dispose: () => void }
    const listeners = new WeakMap<EventTarget, Map<EventListenerOrEventListenerObject, Map<string, Subscription>>>()
    EventTarget.prototype.addEventListener = function (type, listener, options) {
      if (!listener || (this !== window && this !== document)) return nativeAdd.call(this, type, listener, options)
      const config = typeof options === 'object' ? options : { capture: options }
      if (config.signal?.aborted) return
      const target = this === window ? window : document
      const callbacks = listeners.get(target) ?? new Map<EventListenerOrEventListenerObject, Map<string, Subscription>>()
      const owned = callbacks.get(listener) ?? new Map<string, Subscription>()
      const key = `${type}:${Boolean(config.capture)}`
      if (owned.has(key)) return
      const dispose = () => {
        if (owned.delete(key)) counters.subscription_count -= 1
        if (!owned.size) callbacks.delete(listener)
        if (config.signal) nativeRemove.call(config.signal, 'abort', dispose)
      }
      const wrapped: EventListenerOrEventListenerObject = config.once ? function (event) {
        dispose()
        if (typeof listener === 'function') listener.call(target, event)
        else listener.handleEvent(event)
      } : listener
      owned.set(key, { wrapped, dispose }); callbacks.set(listener, owned); listeners.set(target, callbacks)
      counters.subscription_count += 1
      if (config.signal) nativeAdd.call(config.signal, 'abort', dispose, { once: true })
      return nativeAdd.call(target, type, wrapped, options)
    }
    EventTarget.prototype.removeEventListener = function (type, listener, options) {
      const key = `${type}:${typeof options === 'boolean' ? options : Boolean(options?.capture)}`
      const subscription = listener ? listeners.get(this)?.get(listener)?.get(key) : undefined
      subscription?.dispose()
      return nativeRemove.call(this, type, subscription?.wrapped ?? listener, options)
    }
    const nativeCreate = URL.createObjectURL.bind(URL); const nativeRevoke = URL.revokeObjectURL.bind(URL); const urls = new Set<string>()
    URL.createObjectURL = blob => { const url = nativeCreate(blob); urls.add(url); counters.object_url_count = urls.size; return url }
    URL.revokeObjectURL = url => { urls.delete(url); counters.object_url_count = urls.size; nativeRevoke(url) }
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia: async () => {
      const canvas = document.createElement('canvas'); canvas.width = canvas.height = 32
      const stream = canvas.captureStream(1)
      for (const track of stream.getTracks()) {
        counters.media_track_count += 1
        const stop = track.stop.bind(track)
        let stopped = false
        track.stop = () => { if (!stopped) counters.media_track_count -= 1; stopped = true; stop() }
      }
      return stream
    } } })
  })

  const errors: string[] = []
  let backgroundRequests = 0
  page.on('pageerror', error => errors.push(`pageerror:${error.message}`))
  page.on('console', message => { if (message.type() === 'error') errors.push(`console:${message.text()}`) })
  page.on('requestfailed', request => errors.push(`requestfailed:${request.url()}`))
  page.on('request', request => { if (request.url().includes('/api/changes')) backgroundRequests += 1 })

  // This is the sole document load. All transitions in the loop use SPA Links.
  await installOperational(page, {
    realTime: true,
    user: userForRole('mechanic'),
    issue: { ...issue, workflow: { owner: { login: 'mechanic-e2e', display: 'Механик смены' }, review_state: null, display_status: 'in_progress', sync_state: 'synced', has_current_cycle_comment: true } },
    routes: [{ method: 'POST', path: '/api/presence/heartbeat', handler: () => ({ json: { ok: true } }) }],
  })
  await page.goto('/overview?park=7')
  await settlePage(page)
  const documentToken = await page.evaluate(() => Reflect.get(window, '__roboparkSoakDocument'))
  const assertSameDocument = async () => {
    expect(await page.evaluate(() => Reflect.get(window, '__roboparkSoakDocument'))).toBe(documentToken)
  }
  // Prove that instrumentation still wraps application timers, not a virtual
  // clock's internal scheduler, before trusting the samples.
  expect(await page.evaluate(() => {
    const counters = Reflect.get(window, '__roboparkSoak') as SoakCounters
    const before = counters.timer_count
    const interval = window.setInterval(() => {}, 60_000)
    const timeout = window.setTimeout(() => {}, 60_000)
    const during = counters.timer_count
    clearInterval(interval); clearTimeout(timeout)
    return { added: during - before, remaining: counters.timer_count - before }
  })).toEqual({ added: 2, remaining: 0 })
  const started = Date.now(); const deadline = started + durationSeconds * 1000
  const sampleEveryMilliseconds = Math.min(300_000, durationSeconds * 1000)
  let nextSampleAt = started + sampleEveryMilliseconds; let cycles = 0
  const samples: SoakSample[] = []; const operations = new Set<string>()

  const writeSample = async (final = false) => {
    await assertSameDocument()
    const browser = await page.evaluate(async () => {
      const counters = Reflect.get(window, '__roboparkSoak') as SoakCounters
      const storage = await navigator.storage.estimate()
      return { ...counters, storageBytes: storage.usage ?? 0 }
    })
    const elapsed_seconds = final ? durationSeconds : Math.min(durationSeconds, Math.floor((Date.now() - started) / 1000))
    if (samples.at(-1)?.elapsed_seconds === elapsed_seconds) samples.pop()
    samples.push({ ...browser, ...browserResources(), cache_bytes: browser.storageBytes, storage_bytes: browser.storageBytes, db_pool_checked_out: 0, errors: errors.length, operations: [...operations].sort(), elapsed_seconds, cycles, background_requests: backgroundRequests })
    atomicWrite(output!, { format: 2, run_token: runToken, target_seconds: durationSeconds, elapsed_seconds, continuous_contexts: 1, samples })
  }
  const follow = async (name: string) => {
    const link = page.getByRole('link', { name, exact: true }).first()
    await expect(link).toBeVisible()
    const expectedPath = new URL((await link.getAttribute('href'))!, page.url()).pathname
    await link.click()
    await expect(page).toHaveURL(url => url.pathname === expectedPath)
    await assertSameDocument()
    await settlePage(page); operations.add('navigation')
  }

  while (Date.now() < deadline) {
    await follow('Работа'); operations.add('task')
    const issue = page.locator('button[aria-label^="Открыть задачу"]').first()
    await expect(issue).toBeVisible(); await issue.click(); await settlePage(page)
    await expect(page).toHaveURL(/\/work\/ROBOPARK-42(?:\?|$)/)
    await assertSameDocument()
    const check = page.getByRole('tab', { name: 'Проверка', exact: true })
    const task = page.getByRole('tab', { name: 'Задача', exact: true })
    await expect(check).toBeVisible(); await expect(task).toBeVisible()
    await check.click(); await task.click(); operations.add('mode_switch')
    await follow('Роботы'); operations.add('robot')
    const scan = page.getByRole('button', { name: 'Сканировать', exact: true })
    await expect(scan).toBeVisible(); await scan.click()
    const dialog = page.getByRole('dialog', { name: 'Сканировать робота' }); await expect(dialog).toBeVisible()
    await dialog.getByRole('button', { name: 'Включить камеру', exact: true }).click()
    await expect.poll(() => page.evaluate(() => (Reflect.get(window, '__roboparkSoak') as SoakCounters).media_track_count)).toBe(1)
    operations.add('camera')
    await dialog.getByLabel('Выбрать изображение кода', { exact: true }).setInputFiles('public/pwa-icon-192.png')
    await expect(dialog.getByRole('alert')).toContainText('Код не найден на изображении')
    operations.add('photo')
    await dialog.getByRole('button', { name: 'Отменить', exact: true }).click()
    await expect(dialog).toBeHidden()
    await follow('Обзор'); cycles += 1
    expect(await page.evaluate(async () => {
      const name = 'robopark-soak-probe'
      const cache = await caches.open(name)
      await cache.put('/__soak_probe__', new Response('bounded-cache-probe'))
      const value = await (await cache.match('/__soak_probe__'))?.text()
      await caches.delete(name)
      return value
    })).toBe('bounded-cache-probe')
    operations.add('cache')
    if (backgroundRequests > 0) operations.add('background')
    if (Date.now() >= nextSampleAt) { await writeSample(); nextSampleAt += sampleEveryMilliseconds }
  }
  await writeSample(true)
  expect(cycles).toBeGreaterThan(0); expect(errors).toEqual([])
  expect(backgroundRequests).toBeGreaterThan(0)
  expect(['background', 'cache', 'camera', 'photo', 'mode_switch', 'navigation', 'robot', 'task'].every(operation => operations.has(operation))).toBe(true)
  expect(samples.at(-1)?.object_url_count).toBe(0); expect(samples.at(-1)?.media_track_count).toBe(0)
})
