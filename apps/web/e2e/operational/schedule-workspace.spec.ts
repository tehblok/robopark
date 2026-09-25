import { expect, test } from '@playwright/test'
import { userForRole } from './fixtures'
import { openRouteFixture, routeSchedule } from './routeFixtures'

test.use({ hasTouch: true })

test('desktop schedule keeps the team grid sticky across range navigation', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 1000 })
  await openRouteFixture(page, 'schedule', userForRole('royal'), { routes: [{
    method: 'GET',
    path: '/api/schedules/participants',
    handler: () => ({ json: [
      { id: 100, display_name: 'Анна Механик', role: 'mechanic' },
      ...Array.from({ length: 11 }, (_, index) => ({
        id: 110 + index,
        display_name: `Сотрудник ${index + 1}`,
        role: 'mechanic' as const,
      })),
    ] }),
  }] })

  const grid = page.getByTestId('schedule-team-grid')
  await expect(grid).toBeVisible()
  await expect(grid.getByRole('rowheader', { name: 'Анна Механик · Механик' })).toBeVisible()
  const monthView = page.getByRole('button', { name: 'Месяц' })
  await monthView.click()
  await expect(monthView).toHaveAttribute('aria-pressed', 'true')
  await expect.poll(() => grid.getByRole('columnheader').count()).toBeGreaterThan(8)

  const scroller = page.locator('.rp-schedule-team__matrix-scroll')
  const header = grid.locator('.rp-schedule-team__header-group')
  const corner = grid.locator('.rp-schedule-team__corner')
  const employee = grid.locator('.rp-schedule-team__employee').first()
  const before = {
    header: (await header.boundingBox())!,
    corner: (await corner.boundingBox())!,
    employee: (await employee.boundingBox())!,
  }
  const scrollRange = await scroller.evaluate(element => ({
    x: element.scrollWidth - element.clientWidth,
    y: element.scrollHeight - element.clientHeight,
  }))
  expect(scrollRange.x).toBeGreaterThan(0)
  expect(scrollRange.y).toBeGreaterThan(0)
  await scroller.evaluate(element => element.scrollTo({ left: 320, top: 240 }))
  await expect.poll(() => scroller.evaluate(element => ({ left: element.scrollLeft, top: element.scrollTop }))).toMatchObject({ left: 320, top: 240 })
  const after = {
    header: (await header.boundingBox())!,
    corner: (await corner.boundingBox())!,
    employee: (await employee.boundingBox())!,
  }
  expect(Math.abs(after.header.y - before.header.y)).toBeLessThanOrEqual(1)
  expect(Math.abs(after.corner.x - before.corner.x)).toBeLessThanOrEqual(1)
  expect(Math.abs(after.corner.y - before.corner.y)).toBeLessThanOrEqual(1)
  expect(Math.abs(after.employee.x - before.employee.x)).toBeLessThanOrEqual(1)

  const firstDay = grid.getByRole('columnheader').nth(1)
  const initialDay = await firstDay.getAttribute('aria-label')
  await page.getByRole('button', { name: 'Следующий период' }).click()
  await expect(firstDay).not.toHaveAttribute('aria-label', initialDay!)
})

test('royal can edit a team schedule entry', async ({ page }) => {
  let update: unknown
  await page.setViewportSize({ width: 1366, height: 1000 })
  await openRouteFixture(page, 'schedule', userForRole('royal'), { routes: [{
    method: 'PATCH',
    path: `/api/schedules/${routeSchedule.id}`,
    handler: async request => {
      update = await request.json()
      return { json: { ...routeSchedule, ...(update as object) } }
    },
  }] })

  await page.getByRole('button', { name: 'Изменить' }).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { name: 'Изменить период' })).toBeVisible()
  await page.getByLabel('Конец').fill('2026-09-02T22:00')
  await page.getByRole('button', { name: 'Сохранить' }).focus()
  await page.keyboard.press('Enter')

  await expect.poll(() => update).toMatchObject({
    kind: 'shift',
    start_at: new Date('2026-09-02T09:00').toISOString(),
    end_at: new Date('2026-09-02T22:00').toISOString(),
  })
  await expect(page.getByTestId('schedule-team-grid').getByText('09:00 — 22:00')).toBeVisible()
})

for (const width of [360, 390] as const) {
  test(`phone-${width} uses day cards without a compressed grid or document overflow`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 360 ? 800 : 844 })
    await openRouteFixture(page, 'schedule', userForRole('mechanic'))
    await expect(page.getByTestId('schedule-day-cards')).toBeVisible()
    await page.getByRole('button', { name: '2 сентября' }).tap()
    await expect(page.getByText('Моя смена')).toBeVisible()
    await expect(page.getByTestId('schedule-team-grid')).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true)

    await openRouteFixture(page, 'schedule', userForRole('royal'))
    await expect(page.getByTestId('schedule-day-cards')).toBeVisible()
    await expect(page.getByTestId('schedule-team-grid')).toBeHidden()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true)
  })
}
