import { expect, test } from '@playwright/test'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'
import { installOperational, roles, settlePage, userForRole } from './fixtures'

for (const viewport of ['desktop', 'phone'] as const) for (const role of roles) {
  if (viewport === 'phone' && role !== 'operator' && role !== 'admin') continue
  test(`${role}${viewport === 'phone' ? ' phone' : ''}: selected park has an operations queue and SLA`, async ({ page }) => {
    if (viewport === 'phone') await page.setViewportSize({ width: 390, height: 900 })
    await installOperational(page, { user: userForRole(role) })
    await page.goto('/overview?park=7')

    await expect(page.getByRole('heading', { name: 'Смена / Обзор' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Текущие задачи' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Просрочки SLA' })).toBeVisible()
    await expect(page.getByText('Норматив SLA не задан')).toBeVisible()
    const task = page.getByRole('link', { name: 'Открыть задачу ROBOPARK-42' })
    await expect(task).toBeVisible()
    await expect(task).toHaveAttribute('href', '/work/ROBOPARK-42?park=7')
    await expect(page.getByRole('heading', { name: 'Нагрузка по ответственным' })).toHaveCount(role === 'driver' || role === 'mechanic' ? 0 : 1)
    await settlePage(page)
    await assertNoSeriousA11yViolations(page)

    if (viewport === 'phone') {
      await task.scrollIntoViewIfNeeded()
      await expect(task).toBeInViewport()
      expect((await task.boundingBox())!.height).toBeGreaterThanOrEqual(44)
    }
    await task.click()
    await expect(page).toHaveURL(/\/work\/ROBOPARK-42\?park=7/)
    await expect(page.getByRole('heading', { name: 'Задача ROBOPARK-42', exact: true })).toBeVisible()
  })
}

test('royal selected park switches the task request instead of showing a fleet-only summary', async ({ page }) => {
  const requestedParks: string[] = []
  page.on('request', (request) => {
    const url = new URL(request.url())
    if (url.pathname === '/api/operations/overview') requestedParks.push(url.searchParams.get('park_id') ?? '')
  })
  await installOperational(page, { role: 'royal' })
  await page.goto('/overview?park=8')
  await expect(page.getByRole('link', { name: 'Открыть задачу ROBOPARK-42' })).toHaveAttribute('href', '/work/ROBOPARK-42?park=8')
  expect(requestedParks).toContain('8')
})
