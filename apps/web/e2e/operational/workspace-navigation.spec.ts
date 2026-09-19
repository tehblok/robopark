import { expect, test } from '@playwright/test'
import { installOperational, issue, settlePage } from './fixtures'

test('robots navigation remains available while overview is pending', async ({ page }) => {
  await installOperational(page)
  await page.route('**/api/operations/overview**', () => new Promise<void>(() => {}))
  await page.goto('/overview')

  await page.getByRole('link', { name: 'Роботы', exact: true }).click()

  await expect(page).toHaveURL(/\/robots(?:\?.*)?$/, { timeout: 1_000 })
  await expect(page.getByRole('heading', { name: 'Роботы', exact: true })).toBeVisible({ timeout: 1_000 })
})

for (const width of [390, 1440]) {
  test(`shell stays pinned and last work controls remain reachable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 720 })
    await installOperational(page, { role: 'mechanic', listCount: 50, issue: {
      ...issue, description: Array.from({ length: 70 }, () => 'Подробности осмотра робота.').join('\n\n'),
      claim: { park_id: 7 }, workflow: { owner: { login: 'mechanic-e2e', display: 'Механик' }, review_state: null, display_status: 'in_progress', sync_state: 'synced', has_current_cycle_comment: true },
    } })
    await page.goto('/work/ROBOPARK-42?park=7')
    await expect(page.getByRole('heading', { name: issue.summary, exact: true })).toBeVisible()
    await settlePage(page)
    await page.getByRole('button', { name: 'Передать на проверку', exact: true }).scrollIntoViewIfNeeded()
    expect(await page.evaluate(() => window.scrollY)).toBeGreaterThan(100)
    expect((await page.locator('.rp-shell__topbar').boundingBox())?.y).toBe(0)
    if (width >= 900) {
      expect(Math.abs((await page.locator('.rp-shell__sidebar').boundingBox())?.y ?? Number.NaN)).toBeLessThanOrEqual(0.5)
      await page.locator('.rp-shell__sidebar').evaluate(element => { element.scrollTop = element.scrollHeight })
      await expect(page.locator('.rp-shell__desktop-nav').getByRole('link', { name: 'СК и оклейка' })).toBeInViewport()
    } else {
      await expect(page.locator('.rp-shell__bottom-nav')).toBeInViewport()
    }
    await page.getByRole('button', { name: 'Передать на проверку', exact: true }).click()
    await expect(page.getByLabel('Код дефекта')).toBeVisible()
    await page.getByRole('button', { name: 'Отмена', exact: true }).click()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  })
}

test('related repairs retain robot scope without the main blocker filters', async ({ page }) => {
  const queries: URLSearchParams[] = []
  page.on('request', request => {
    const url = new URL(request.url())
    if (url.pathname === '/api/tracker/issues' && url.searchParams.has('robot_exact')) queries.push(url.searchParams)
  })
  await installOperational(page)
  await page.goto('/work/ROBOPARK-42?park=7&status=closed&assignee=other&age=24&page=2')
  await page.getByRole('tab', { name: 'Открытые задачи', exact: true }).click()
  await expect.poll(() => queries.length).toBe(1)
  expect(Object.fromEntries(queries[0])).toMatchObject({ park: 'north', queue: 'ROBOPARK', robot_exact: '447', related_repairs: 'true', sort: 'oldest', offset: '0' })
  expect(queries[0].has('status')).toBe(false)
  expect(queries[0].has('assignee')).toBe(false)
  expect(queries[0].has('age_hours')).toBe(false)
})

test('nested work keeps parent navigation and loads each repair tab only when selected', async ({ page }) => {
  const queries: URLSearchParams[] = []
  page.on('request', request => {
    const url = new URL(request.url())
    if (url.pathname === '/api/tracker/issues' && url.searchParams.has('robot_exact')) queries.push(url.searchParams)
  })
  await page.setViewportSize({ width: 1440, height: 900 })
  await installOperational(page)
  await page.goto('/work/ROBOPARK-42?park=7')
  await expect(page.getByRole('tab', { name: 'Задача', exact: true })).toHaveAttribute('aria-selected', 'true')
  expect(queries).toEqual([])
  await page.getByRole('tab', { name: 'Открытые задачи', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Открытые задачи робота 447' })).toBeVisible()
  await expect.poll(() => queries.length).toBe(1)
  await page.getByRole('tab', { name: 'Закрытые задачи', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Закрытые задачи робота 447' })).toBeVisible()
  await expect.poll(() => queries.length).toBe(2)
  expect(queries.map(params => params.get('status'))).toEqual([null, 'closed'])
  await expect(page.locator('.rp-shell__desktop-nav').getByRole('link', { name: 'Работа', exact: true })).toHaveAttribute('aria-current', 'page')
  // This is the root repair itself; a parent link exists only on a child repair.
  await page.getByRole('tab', { name: 'Задача', exact: true }).click()
  await expect(page.getByRole('tab', { name: 'Задача', exact: true })).toHaveAttribute('aria-selected', 'true')
})
