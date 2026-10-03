import { expect, test } from '@playwright/test'
import { installOperational, issue } from './operational/fixtures'

const repair = { ...issue, claim: { park_id: 7 }, workflow: {
  owner: { login: 'mechanic-e2e', display: 'Механик смены' }, review_state: null,
  display_status: 'in_progress' as const, sync_state: 'synced' as const, has_current_cycle_comment: false,
} }

for (const width of [390, 1440]) {
  test(`ordinary browser queues work offline and recovers after reconnect at ${width}px`, async ({ page, context }) => {
    const submittedActions: string[] = []
    await page.setViewportSize({ width, height: 900 })
    await installOperational(page, { issue: repair, routes: [{
      method: 'POST', path: '/api/sync/batch', handler: async request => {
        const body = await request.json() as { actions: { client_action_id: string }[] }
        submittedActions.push(...body.actions.map(action => action.client_action_id))
        return { json: {
          results: body.actions.map(action => ({ client_action_id: action.client_action_id, state: 'confirmed', code: null, result: {} })),
          deltas: {}, revisions: {}, revoked_scopes: [],
        } }
      },
    }] })
    await page.goto('/work/ROBOPARK-42?park=7')
    expect(await page.evaluate(() => navigator.serviceWorker.controller)).toBeNull()
    await page.getByRole('button', { name: 'История и сообщения' }).click()
    const composer = page.getByRole('textbox', { name: 'Комментарии', exact: true })
    await expect(composer).toBeVisible()

    await context.setOffline(true)
    await composer.fill('Колесо заменено, крепление проверено')
    await page.getByRole('button', { name: 'Отправить' }).click()
    await expect(page.getByText('Колесо заменено, крепление проверено')).toBeVisible()
    expect(submittedActions).toEqual([])

    await context.setOffline(false)
    await page.evaluate(() => window.dispatchEvent(new Event('online')))
    // Empty reconciliation batches are allowed; the user's command is sent once.
    await expect.poll(() => submittedActions.length).toBe(1)
    await page.reload()
    await page.getByRole('button', { name: 'История и сообщения' }).click()
    await expect(page.getByRole('textbox', { name: 'Комментарии', exact: true })).toBeVisible()
    expect(submittedActions).toHaveLength(1)
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
