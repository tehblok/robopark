import { expect, test } from '@playwright/test'
import type { TrackerIssueDetail } from '../../src/api'
import { installOperational, issue } from './fixtures'

const claimed: TrackerIssueDetail = {
  ...issue,
  assignee: { display: 'mechanic-e2e', login: 'mechanic-e2e' },
  claim: { park_id: 7 },
  workflow: {
    owner: { display: 'mechanic-e2e', login: 'mechanic-e2e' }, review_state: null,
    display_status: 'in_progress', sync_state: 'saved', has_current_cycle_comment: false,
  },
}

test('mobile handoff disclosure submits one lifecycle command on duplicate activation', async ({ page }) => {
  const keys: string[] = []
  await page.setViewportSize({ width: 390, height: 844 })
  await installOperational(page, { issue: claimed, routes: [
    { method: 'GET', path: '/api/tracker/issues/ROBOPARK-42/timeline', handler: () => ({ json: [] }) },
    { method: 'GET', path: '/api/tracker/defect-codes', handler: () => ({ json: [] }) },
    { method: 'POST', path: '/api/sync/batch', handler: async request => {
      const batch = await request.json() as { actions: { client_action_id: string; action: string }[] }
      keys.push(...batch.actions.filter(action => action.action === 'handoff').map(action => action.client_action_id))
      return { json: {
        results: batch.actions.map(action => ({ client_action_id: action.client_action_id, state: 'confirmed', code: null, result: {} })),
        deltas: {}, revisions: {}, revoked_scopes: [],
      } }
    } },
  ] })
  await page.goto('/work/ROBOPARK-42?park=7')

  const disclosure = page.getByRole('button', { name: 'Передать смену', exact: true }).first()
  await expect(disclosure).toHaveAttribute('aria-expanded', 'false')
  await disclosure.click()
  await page.getByLabel('Логин сменщика').fill('mechanic-next')
  await page.getByLabel('Причина передачи').fill('Смена завершена')
  const submit = page.getByRole('button', { name: 'Передать смену', exact: true }).last()
  expect((await submit.boundingBox())?.height).toBeGreaterThanOrEqual(44)
  await submit.dblclick()
  await expect.poll(() => keys.length).toBe(1)
  expect(keys[0].length).toBeGreaterThanOrEqual(8)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})
