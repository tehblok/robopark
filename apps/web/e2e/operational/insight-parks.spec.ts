import { expect, test } from '@playwright/test'
import { analyticsFixture } from '../../src/domains/analytics/analytics.test-support'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'
import { installOperational, parkNorth, parkSouth, userForRole } from './fixtures'

for (const role of ['admin', 'royal', 'operator'] as const) for (const section of ['overview', 'analytics']) {
  test(`${role} selects all accessible parks and one park in ${section}`, async ({ page }, info) => {
    await page.setViewportSize({ width: role === 'operator' ? 390 : 1440, height: 900 })
    const user = userForRole(role)
    const foreign = { ...parkNorth, id: 99, name: 'Недоступный парк', tag: 'foreign' }
    const requests: number[] = []
    page.on('request', request => {
      const url = new URL(request.url())
      if (url.pathname === `/api/${section === 'overview' ? 'operations/overview' : 'analytics'}`) requests.push(Number(url.searchParams.get('park_id')))
    })
    await installOperational(page, { user, parks: role === 'operator' ? [parkNorth, parkSouth, foreign] : [parkNorth, parkSouth], routes: [
      { method: 'GET', path: '/api/analytics', handler: request => ({ json: analyticsFixture(Number(new URL(request.url).searchParams.get('park_id')), 7, '1d') }) },
    ] })
    await page.goto(`/${section}`)
    await expect(page).toHaveURL(/park=all/)
    await expect.poll(() => [...new Set(requests)].sort()).toEqual([7, 8])
    const region = (name: string) => page.getByRole('region', { name: section === 'analytics' ? `История парка ${name}` : name, exact: true })
    await expect(region(parkNorth.name)).toBeVisible()
    await expect(region(parkSouth.name)).toBeVisible()
    await assertNoSeriousA11yViolations(page)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: info.outputPath(`${section}-${role}-all.png`) })
    await page.getByRole('button', { name: 'Сменить парк' }).click()
    await expect(page.getByRole('option', { name: foreign.name })).toHaveCount(0)
    await page.getByRole('option', { name: parkSouth.name, exact: true }).click()
    await expect(page).toHaveURL(/park=8/)
    if (section === 'analytics') await expect(region(parkNorth.name)).toHaveCount(0)
    else await expect(page.getByRole('region', { name: parkNorth.name, exact: true })).toHaveCount(0)
    await expect.poll(() => requests.at(-1)).toBe(8)
    await page.getByRole('button', { name: 'Сменить парк' }).click()
    await page.getByRole('option', { name: 'Все доступные парки', exact: true }).click()
    await expect(page).toHaveURL(/park=all/)
    await expect(region(parkNorth.name)).toBeVisible()
    await expect(region(parkSouth.name)).toBeVisible()
    expect(requests).not.toContain(99)
  })
}
