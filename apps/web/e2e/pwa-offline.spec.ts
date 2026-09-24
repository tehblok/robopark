import { expect, test } from '@playwright/test'
import { installOperational, issue } from './operational/fixtures'

const repair = { ...issue, claim: { park_id: 7 }, workflow: {
  owner: { login: 'mechanic-e2e', display: 'Механик смены' }, review_state: null,
  display_status: 'in_progress' as const, sync_state: 'synced' as const, has_current_cycle_comment: false,
} }

for (const width of [390, 1440]) {
  test(`ordinary browser queues work offline and recovers after reconnect at ${width}px`, async ({ page, context }) => {
    let syncRequests = 0
    await page.setViewportSize({ width, height: 900 })
    await installOperational(page, { issue: repair, routes: [{
      method: 'POST', path: '/api/sync/batch', handler: async request => {
        syncRequests += 1
        const body = await request.json() as { actions: { client_action_id: string }[] }
        return { json: {
          results: body.actions.map(action => ({ client_action_id: action.client_action_id, state: 'confirmed', code: null, result: {} })),
          deltas: {}, revisions: {}, revoked_scopes: [],
        } }
      },
    }] })
    await page.goto('/work/ROBOPARK-42?park=7')
    expect(await page.evaluate(() => navigator.serviceWorker.controller)).toBeNull()
    await page.getByRole('tab', { name: 'Чат' }).click()
    const composer = page.getByRole('textbox', { name: 'Комментарии', exact: true })
    await expect(composer).toBeVisible()

    await context.setOffline(true)
    await composer.fill('Колесо заменено, крепление проверено')
    await page.getByRole('button', { name: 'Отправить' }).click()
    await expect(page.getByText('Колесо заменено, крепление проверено')).toBeVisible()
    expect(syncRequests).toBe(0)

    await context.setOffline(false)
    await page.evaluate(() => window.dispatchEvent(new Event('online')))
    await expect.poll(() => syncRequests).toBe(1)
    await page.reload()
    await page.getByRole('tab', { name: 'Чат' }).click()
    await expect(page.getByRole('textbox', { name: 'Комментарии', exact: true })).toBeVisible()
    expect(await page.evaluate(() => navigator.serviceWorker.controller)).toBeNull()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  })
}

test('manifest exposes install shortcuts and a same-origin photo share target', async ({ page }) => {
  const manifest = await (await page.request.get('/manifest.webmanifest')).json() as {
    shortcuts?: { url: string }[]
    share_target?: { action: string, method: string, enctype: string }
  }
  expect(manifest.shortcuts?.map(item => item.url)).toEqual(expect.arrayContaining(['/work?view=mine', '/robots?scan=1', '/inventory']))
  expect(manifest.share_target).toMatchObject({ action: '/share-target', method: 'POST', enctype: 'multipart/form-data' })
})
