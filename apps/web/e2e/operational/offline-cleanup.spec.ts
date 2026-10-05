import { expect, test } from '../support/persistentWebKit'

test('offline cleanup preserves a concurrent entity refresh after reload', async ({ page }) => {
  await page.route('**/__offline_cleanup__', route => route.fulfill({
    contentType: 'text/html', body: '<!doctype html><title>Offline cleanup</title>',
  }))
  await page.goto('/__offline_cleanup__')
  const result = await page.evaluate(async () => {
    const moduleUrl = '/src/pwa/offlineDb.ts'
    const { openOfflineDb } = await import(moduleUrl) as typeof import('../../src/pwa/offlineDb')
    const scope = { account: 'cleanup-probe', role: 'mechanic', permissions: 'tracker.read', park: '7', schema: 1 }
    const cleaning = await openOfflineDb(scope)
    const writing = await openOfflineDb(scope)
    await cleaning.putEntity('task:updated', { title: 'old' }, { updatedAt: 1 })
    const originalGetAll = IDBIndex.prototype.getAll
    let concurrentWrite: Promise<boolean> | undefined
    let intercepted = false
    IDBIndex.prototype.getAll = function (...args) {
      const request = originalGetAll.apply(this, args)
      if (this.objectStore.name === 'entities' && !intercepted) {
        intercepted = true
        request.addEventListener('success', () => {
          concurrentWrite = writing.putEntity('task:updated', { title: 'fresh' }, { updatedAt: 10_000 })
        }, { once: true })
      }
      return request
    }
    try {
      await cleaning.cleanup({ maxBytes: 1_000_000, now: 10_000, entityTtlMs: 100 })
      if (!concurrentWrite || !await concurrentWrite) throw new Error('concurrent write did not commit')
      return await writing.getEntity('task:updated')
    } finally {
      IDBIndex.prototype.getAll = originalGetAll
      cleaning.close()
      writing.close()
    }
  })
  expect(result).toEqual({ title: 'fresh' })
  await page.reload()
  const restored = await page.evaluate(async () => {
    const moduleUrl = '/src/pwa/offlineDb.ts'
    const { openOfflineDb } = await import(moduleUrl) as typeof import('../../src/pwa/offlineDb')
    const db = await openOfflineDb({ account: 'cleanup-probe', role: 'mechanic', permissions: 'tracker.read', park: '7', schema: 1 })
    try { return await db.getEntity('task:updated') } finally { db.close() }
  })
  expect(restored).toEqual({ title: 'fresh' })
})
