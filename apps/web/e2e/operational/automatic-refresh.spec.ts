import { expect, test } from '@playwright/test'
import { installOperational, issue } from './fixtures'

test('task and comments update automatically without losing a comment draft', async ({ page }) => {
  let revision = 0
  let details = 0
  // Timer control must be installed before the application creates intervals.
  // setFixedTime only freezes Date; installing runFor's clock after navigation
  // leaves the existing polling timers outside the controlled scheduler.
  await page.clock.install({ time: new Date('2026-09-02T09:05:00Z') })
  await installOperational(page, { realTime: true, routes: [
    { method: 'GET', path: '/api/tracker/issues/ROBOPARK-42', handler: () => {
      details += 1
      return { json: { ...issue, claim: { park_id: 7 }, workflow: {
        owner: { login: 'mechanic-e2e', display: 'Механик смены' }, review_state: null,
        display_status: 'in_progress', sync_state: 'synced', has_current_cycle_comment: true,
      }, description: revision ? 'Новое описание от коллеги' : 'Исходное описание' } }
    } },
    { method: 'GET', path: '/api/tracker/issues/ROBOPARK-42/timeline', handler: () => ({ json: revision ? [{
      id: 'incoming', kind: 'tracker', text: 'Комментарий коллеги', author: 'Механик смены', sync_state: 'synced',
      created_at: '2026-09-02T09:06:00Z', attachments: [],
    }] : [] }) },
  ] })
  await page.goto('/work/ROBOPARK-42?park=7&status=queued')
  await expect(page.getByText('Исходное описание', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'История и сообщения' }).click()
  const draft = page.getByRole('textbox', { name: 'Комментарии', exact: true })
  await draft.fill('Мой незавершённый комментарий')
  const initialDetails = details
  revision = 1
  await page.clock.runFor(60_000)
  await expect(page.getByText('Новое описание от коллеги', { exact: true })).toBeVisible()
  await expect(page.getByText('Комментарий коллеги', { exact: true })).toBeVisible()
  await expect(draft).toHaveValue('Мой незавершённый комментарий')
  await expect(page.getByRole('button', { name: /^Обновить/ })).toHaveCount(0)
  expect(details).toBeGreaterThan(initialDetails)
  expect(details).toBeLessThanOrEqual(initialDetails + 2)
})
