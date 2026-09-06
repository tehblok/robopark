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
    await installOperational(page, { role: 'admin', listCount: 50, issue: {
      ...issue, description: Array.from({ length: 70 }, () => 'Подробности осмотра робота.').join('\n\n'),
    } })
    await page.goto('/work/ROBOPARK-42?park=7')
    await expect(page.getByRole('heading', { name: issue.summary, exact: true })).toBeVisible()
    await settlePage(page)
    await page.getByRole('button', { name: 'Закрыть тикет', exact: true }).scrollIntoViewIfNeeded()
    expect(await page.evaluate(() => window.scrollY)).toBeGreaterThan(100)
    expect((await page.locator('.rp-shell__topbar').boundingBox())?.y).toBe(0)
    if (width >= 900) {
      expect(Math.abs((await page.locator('.rp-shell__sidebar').boundingBox())?.y ?? Number.NaN)).toBeLessThanOrEqual(0.5)
      await page.locator('.rp-shell__sidebar').evaluate(element => { element.scrollTop = element.scrollHeight })
      await expect(page.locator('.rp-shell__desktop-nav').getByRole('link', { name: 'Настройка проверки робота' })).toBeInViewport()
    } else {
      await expect(page.locator('.rp-shell__bottom-nav')).toBeInViewport()
    }
    await page.getByRole('button', { name: 'Закрыть тикет', exact: true }).click()
    await expect(page.getByRole('alertdialog', { name: 'Закрыть задачу?' })).toBeVisible()
    await page.getByRole('button', { name: 'Отмена', exact: true }).click()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  })
}

test('robot remaining-work link opens oldest scoped work with only the robot filter', async ({ page }) => {
  const queries: URLSearchParams[] = []
  page.on('request', request => {
    const url = new URL(request.url())
    if (url.pathname === '/api/tracker/issues') queries.push(url.searchParams)
  })
  await installOperational(page)
  await page.goto('/work/ROBOPARK-42?park=7&status=closed&assignee=other&age=24&page=2')
  await page.getByRole('link', { name: 'Незавершённые задачи робота 447' }).click()
  await expect(page).toHaveURL(/\/work\?park=7&queue=ROBOPARK&robot=447$/)
  await expect.poll(() => queries.some((params) => (
    params.get('robot') === '447'
      && params.get('limit') === '50'
      && !params.has('status')
  ))).toBe(true)
  const scoped = queries.findLast((params) => (
    params.get('robot') === '447'
      && params.get('limit') === '50'
      && !params.has('status')
  ))!
  expect(Object.fromEntries(scoped)).toMatchObject({ park: 'north', queue: 'ROBOPARK', robot: '447', sort: 'oldest', offset: '0' })
  expect(scoped.has('status')).toBe(false)
  expect(scoped.has('assignee')).toBe(false)
})

test('nested work keeps its parent navigation and loads related robot tasks after the selected issue', async ({ page }) => {
  const queries: URLSearchParams[] = []
  page.on('request', request => {
    const url = new URL(request.url())
    if (url.pathname === '/api/tracker/issues') queries.push(url.searchParams)
  })
  await page.setViewportSize({ width: 1440, height: 900 })
  await installOperational(page)
  await page.goto('/work/ROBOPARK-42?park=7')

  await expect(page.getByRole('heading', { name: 'Открытые задачи робота 447' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Последние закрытые задачи робота 447' })).toBeVisible()
  await expect.poll(() => queries.filter((params) => params.get('robot') === '447').length).toBe(2)
  expect(queries.filter((params) => params.get('robot') === '447').map((params) => params.get('status')))
    .toEqual([null, 'closed'])
  await expect(page.locator('.rp-shell__desktop-nav').getByRole('link', { name: 'Работа', exact: true }))
    .toHaveAttribute('aria-current', 'page')

  await page.getByRole('link', { name: 'Незавершённые задачи робота 447' }).click()
  await expect(page).toHaveURL('/work?park=7&queue=ROBOPARK&robot=447')
})
