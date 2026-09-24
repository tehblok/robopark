import { expect, test } from '@playwright/test'
import { userForRole } from './fixtures'
import { openRouteFixture, routeSchedule } from './routeFixtures'

test('desktop schedule keeps the team grid sticky across range navigation', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await openRouteFixture(page, 'schedule', userForRole('royal'))

  const grid = page.getByTestId('schedule-team-grid')
  await expect(grid).toBeVisible()
  await expect(grid.getByRole('rowheader', { name: 'Анна Механик · Механик' })).toBeVisible()
  const monthView = page.getByRole('button', { name: 'Месяц' })
  await monthView.click()
  await expect(monthView).toHaveAttribute('aria-pressed', 'true')
  await expect.poll(() => grid.getByRole('columnheader').count()).toBeGreaterThan(8)

  const stickyGeometry = await grid.evaluate(element => {
    const header = element.querySelector<HTMLElement>('.rp-schedule-team__header-group')
    const corner = element.querySelector<HTMLElement>('.rp-schedule-team__corner')
    const employee = element.querySelector<HTMLElement>('.rp-schedule-team__employee')
    return {
      header: header ? getComputedStyle(header).position : null,
      corner: corner ? getComputedStyle(corner).position : null,
      employee: employee ? getComputedStyle(employee).position : null,
      widerThanViewport: element.scrollWidth > element.parentElement!.clientWidth,
    }
  })
  expect(stickyGeometry).toEqual({ header: 'sticky', corner: 'sticky', employee: 'sticky', widerThanViewport: true })

  const firstDay = grid.getByRole('columnheader').nth(1)
  const initialDay = await firstDay.getAttribute('aria-label')
  await page.getByRole('button', { name: 'Следующий период' }).click()
  await expect(firstDay).not.toHaveAttribute('aria-label', initialDay!)
})

test('royal can edit a team schedule entry', async ({ page }) => {
  let update: unknown
  await page.setViewportSize({ width: 1440, height: 1000 })
  await openRouteFixture(page, 'schedule', userForRole('royal'), { routes: [{
    method: 'PATCH',
    path: `/api/schedules/${routeSchedule.id}`,
    handler: async request => {
      update = await request.json()
      return { json: { ...routeSchedule, ...(update as object) } }
    },
  }] })

  await page.getByRole('button', { name: 'Изменить' }).click()
  await expect(page.getByRole('heading', { name: 'Изменить период' })).toBeVisible()
  await page.getByLabel('Конец').fill('2026-09-23T22:00')
  await page.getByRole('button', { name: 'Сохранить' }).click()

  await expect.poll(() => update).toMatchObject({
    kind: 'shift',
    start_at: '2026-09-23T09:00:00+03:00',
    end_at: '2026-09-23T22:00:00+03:00',
  })
  await expect(page.getByText('09:00 — 22:00')).toBeVisible()
})

for (const width of [360, 390] as const) {
  test(`phone-${width} uses day cards without a compressed grid or document overflow`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 360 ? 800 : 844 })
    await openRouteFixture(page, 'schedule', userForRole('mechanic'))
    await expect(page.getByTestId('schedule-day-cards')).toBeVisible()
    await expect(page.getByText('Моя смена')).toBeVisible()
    await expect(page.getByTestId('schedule-team-grid')).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true)

    await openRouteFixture(page, 'schedule', userForRole('royal'))
    await expect(page.getByTestId('schedule-day-cards')).toBeVisible()
    await expect(page.getByTestId('schedule-team-grid')).toBeHidden()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true)
  })
}
