import { expect, test } from '@playwright/test'
import type { TaskTimelineItem, TrackerIssueDetail } from '../../src/api'
import { FIXED_TIME, installOperational, issue } from './fixtures'

const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAACklEQVR4nGMAAQAABQABDQottAAAAABJRU5ErkJggg==', 'base64')

const claimed: TrackerIssueDetail = {
  ...issue,
  assignee: { display: 'mechanic-e2e', login: 'mechanic-e2e' },
  claim: { park_id: 7 },
  workflow: {
    owner: { display: 'mechanic-e2e', login: 'mechanic-e2e' },
    review_state: null,
    display_status: 'in_progress',
    sync_state: 'pending',
    has_current_cycle_comment: true,
  },
}

test('390x844 lifecycle card is ordered, keyboard reachable and does not overflow', async ({ page }) => {
  const timeline: TaskTimelineItem[] = [{
    id: 'message-1', kind: 'user', author: 'Механик', text: 'Крепление заменено',
    created_at: FIXED_TIME, sync_state: 'pending', attachments: [],
  }]
  await page.setViewportSize({ width: 390, height: 844 })
  await installOperational(page, { issue: claimed, routes: [
    { method: 'GET', path: '/api/tracker/issues/ROBOPARK-42/timeline', handler: () => ({ json: timeline }) },
    { method: 'GET', path: '/api/tracker/defect-codes', handler: () => ({ json: [{ code: 'BD-01', label: 'Вмятина', description: null }] }) },
  ] })
  await page.goto('/work/ROBOPARK-42?park=7')

  const composer = page.getByRole('textbox', { name: 'Комментарии', exact: true })
  const review = page.getByRole('button', { name: 'Передать на проверку', exact: true })
  const parts = page.getByRole('button', { name: 'Заказать запчасть', exact: true })
  const handoff = page.getByRole('button', { name: 'Передать смену', exact: true }).first()
  await expect(composer).toBeVisible()
  await expect(page.getByRole('status').filter({ hasText: 'Отправляется в Tracker' }).first()).toBeVisible()
  await expect(parts).toHaveAttribute('aria-expanded', 'false')
  await expect(handoff).toHaveAttribute('aria-expanded', 'false')
  const positions = await Promise.all([composer, review, parts, handoff].map(locator => locator.evaluate(element => element.getBoundingClientRect().top)))
  expect(positions).toEqual([...positions].sort((a, b) => a - b))

  for (const control of [page.getByRole('button', { name: 'Отправить', exact: true }), review, parts, handoff]) {
    const box = await control.boundingBox()
    expect(box?.height).toBeGreaterThanOrEqual(44)
  }
  await review.click()
  await page.getByLabel('Выбрать файл').setInputFiles({ name: 'fixed.png', mimeType: 'image/png', buffer: PNG })
  await expect(page.getByLabel('Выбрать файл')).toHaveValue(/fixed\.png$/)
  await parts.focus()
  await page.keyboard.press('Enter')
  await expect(parts).toBeFocused()
  await expect(parts).toHaveAttribute('aria-expanded', 'true')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})

test('robot repair descriptions keep classification, comment, zone and repair notes only', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await installOperational(page, { issue: { ...issue, description: `**SUF**: робот находится под управлением системы SUF
**Classificator:** Disk space
**Comment:** Недостаточно места на диске
**Zone:** Lavka Smolensky
**Port:** Moscow Robot
**Rover name:** a1217
**Mode:** AUTO_MODE_AUTO
Что было сделано: Агрессивная чистка, почищены старые докер-образы. Рекомендации: Забит логами, необходимо слить по шнурку.` } })
  await page.goto('/work/ROBOPARK-42?park=7')
  const description = page.locator('.issue-section').filter({ has: page.getByRole('heading', { name: 'Описание', exact: true }) })
  await expect(description).toContainText('Classificator: Disk space')
  await expect(description).toContainText('Comment: Недостаточно места на диске')
  await expect(description).toContainText('Zone: Lavka Smolensky')
  await expect(description).toContainText('Что было сделано: Агрессивная чистка, почищены старые докер-образы.')
  await expect(description).toContainText('Рекомендации: Забит логами, необходимо слить по шнурку.')
  await expect(description).not.toContainText('SUF')
  await expect(description).not.toContainText('Port:')
  await expect(description).not.toContainText('AUTO_MODE_AUTO')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})
