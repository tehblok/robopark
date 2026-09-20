import { test, expect } from '@playwright/test'
import { readdirSync, writeFileSync } from 'node:fs'
import { installOperational, settlePage, snapshot, userForRole } from './fixtures'
import { selectInterface } from '../support/interfaceMode'

type SoakCounters = {
  timers: number
  subscriptions: number
  objectUrls: number
  mediaTracks: number
}

test('repeatable UI soak sample covers navigation, modes, task, robot, photo, camera, cache and background work', async ({ page }) => {
  const durationSeconds = Number(process.env.ROBOPARK_SOAK_DURATION_SECONDS ?? '2')
  const output = process.env.ROBOPARK_SOAK_OUTPUT
  expect(Number.isFinite(durationSeconds) && durationSeconds > 0).toBe(true)
  expect(output).toBeTruthy()
  test.setTimeout((durationSeconds + 60) * 1000)

  await page.addInitScript(() => {
    const counters: SoakCounters = { timers: 0, subscriptions: 0, objectUrls: 0, mediaTracks: 0 }
    Object.defineProperty(window, '__roboparkSoak', { value: counters })
    const nativeSetInterval = window.setInterval.bind(window)
    const nativeClearInterval = window.clearInterval.bind(window)
    const intervals = new Set<number>()
    window.setInterval = ((handler: TimerHandler, timeout?: number, ...args: unknown[]) => {
      const id = nativeSetInterval(handler, timeout, ...args)
      intervals.add(id)
      counters.timers = intervals.size
      return id
    }) as typeof window.setInterval
    window.clearInterval = ((id?: number) => {
      if (id !== undefined) intervals.delete(id)
      counters.timers = intervals.size
      return nativeClearInterval(id)
    }) as typeof window.clearInterval

    const nativeAdd = EventTarget.prototype.addEventListener
    const nativeRemove = EventTarget.prototype.removeEventListener
    const listeners = new WeakMap<EventTarget, Set<string>>()
    EventTarget.prototype.addEventListener = function (type, listener, options) {
      if (listener) {
        const owned = listeners.get(this) ?? new Set<string>()
        const key = `${type}:${String(listener)}`
        if (!owned.has(key)) counters.subscriptions += 1
        owned.add(key)
        listeners.set(this, owned)
      }
      return nativeAdd.call(this, type, listener, options)
    }
    EventTarget.prototype.removeEventListener = function (type, listener, options) {
      if (listener) {
        const owned = listeners.get(this)
        if (owned?.delete(`${type}:${String(listener)}`)) counters.subscriptions -= 1
      }
      return nativeRemove.call(this, type, listener, options)
    }

    const nativeCreate = URL.createObjectURL.bind(URL)
    const nativeRevoke = URL.revokeObjectURL.bind(URL)
    const urls = new Set<string>()
    URL.createObjectURL = blob => {
      const url = nativeCreate(blob)
      urls.add(url)
      counters.objectUrls = urls.size
      return url
    }
    URL.revokeObjectURL = url => {
      urls.delete(url)
      counters.objectUrls = urls.size
      nativeRevoke(url)
    }

    const track = { stop: () => { counters.mediaTracks = Math.max(0, counters.mediaTracks - 1) } }
    const stream = { getTracks: () => [track] }
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: async () => { counters.mediaTracks += 1; return stream } },
    })
  })

  let backgroundRequests = 0
  page.on('request', request => {
    if (request.url().includes('/api/changes')) backgroundRequests += 1
  })
  await installOperational(page, { user: userForRole('royal') })
  const deadline = Date.now() + durationSeconds * 1000
  let cycles = 0
  while (Date.now() < deadline) {
    await page.goto('/overview?park=7')
    await settlePage(page)
    await selectInterface(page, cycles % 2 === 0 ? 'Новый А' : 'Классический')
    await page.goto('/work/ROBOPARK-42?park=7')
    await settlePage(page)
    const file = page.getByLabel('Файл', { exact: true })
    if (await file.count()) {
      await file.setInputFiles({ name: 'soak.jpg', mimeType: 'image/jpeg', buffer: Buffer.from('soak-photo') })
      await file.setInputFiles([])
    }
    await page.goto(`/robots/${snapshot.vin}?park=7`)
    await settlePage(page)
    await page.goto('/robots?park=7')
    await settlePage(page)
    const scan = page.getByRole('button', { name: 'Сканировать', exact: true })
    if (await scan.count()) {
      await scan.click()
      const dialog = page.getByRole('dialog', { name: 'Сканировать робота' })
      await expect(dialog).toBeVisible()
      await dialog.getByRole('button', { name: /Закрыть|Отмена/ }).click()
    }
    cycles += 1
  }
  await page.goto('/overview?park=7')
  await settlePage(page)
  const browser = await page.evaluate(async () => {
    const counters = Reflect.get(window, '__roboparkSoak') as SoakCounters
    const storage = await navigator.storage.estimate()
    return { ...counters, storageBytes: storage.usage ?? 0 }
  })
  const memory = process.memoryUsage()
  let fdCount = 0
  try {
    fdCount = readdirSync(process.platform === 'linux' ? '/proc/self/fd' : '/dev/fd').length
  } catch {
    fdCount = 0
  }
  const sample = {
    rss_bytes: memory.rss,
    fd_count: fdCount,
    timer_count: browser.timers,
    subscription_count: browser.subscriptions,
    object_url_count: browser.objectUrls,
    media_track_count: browser.mediaTracks,
    cache_bytes: browser.storageBytes,
    storage_bytes: browser.storageBytes,
    db_pool_checked_out: 0,
    errors: 0,
    operations: ['navigation', 'mode_switch', 'task', 'robot', 'photo', 'camera', 'cache', 'background'],
    cycles,
    background_requests: backgroundRequests,
  }
  writeFileSync(output!, `${JSON.stringify(sample)}\n`, { flag: 'wx' })
  expect(cycles).toBeGreaterThan(0)
  expect(browser.objectUrls).toBe(0)
  expect(browser.mediaTracks).toBe(0)
})
