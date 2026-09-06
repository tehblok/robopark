import { expect, test } from '@playwright/test'
import { analyticsFixture } from '../../src/domains/analytics/analytics.test-support'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'
import { installOperational, settlePage } from './fixtures'

for (const theme of ['light', 'dark'] as const) for (const width of [320, 1440]) {
  test(`historical analytics ${theme} ${width}: filters, coverage, comparison and drilldown`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    await page.addInitScript(theme => localStorage.setItem('robopark-theme', theme), theme)
    const requests: string[] = []
    page.on('request', request => {
      if (new URL(request.url()).pathname === '/api/operations/overview') requests.push(request.url())
    })
    await installOperational(page, { role: 'operator', routes: [{ method: 'GET', path: '/api/analytics', handler: request => {
      const params = new URL(request.url).searchParams
      return { json: analyticsFixture(Number(params.get('park_id')), Number(params.get('days')), params.get('bucket') === '2h' ? '2h' : '1d') }
    } }] })
    await page.goto('/analytics?park=7')
    await expect(page.getByRole('heading', { name: 'Динамика процесса' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Текущие задачи' })).toHaveCount(0)
    await expect(page.getByText('Неполная история', { exact: true })).toBeVisible()
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
    await page.getByLabel('Период аналитики').selectOption('1')
    await page.getByLabel('Шаг графиков').selectOption('2h')
    await expect(page).toHaveURL(/period=1&bucket=2h/)
    const intervals = page.getByText('Значения по интервалам', { exact: true }).first()
    await intervals.click()
    await expect(page.getByRole('table', { name: 'Поступило за период · МСК' }).getByText('Нет наблюдений').first()).toBeVisible()
    await intervals.click()
    await page.getByLabel('Сравнить с парком').selectOption('8')
    await expect(page.getByRole('table', { name: 'Сравнение парков' })).toBeVisible()
    await expect(page.getByRole('region', { name: 'История парка Южный парк' })).toBeVisible()
    await settlePage(page)
    await assertNoSeriousA11yViolations(page)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.screenshot({ path: testInfo.outputPath(`analytics-${theme}-${width}.png`) })
    const history = page.getByRole('region', { name: 'История парка Южный парк' })
    await history.getByText('Задачи в наблюдениях (1)', { exact: true }).click()
    const task = history.getByRole('link', { name: 'Открыть задачу ROBOPARK-42' }).filter({ visible: true })
    await expect(task).toHaveAttribute('href', '/work/ROBOPARK-42?park=8')
    expect((await task.boundingBox())!.height).toBeGreaterThanOrEqual(44)
    await task.click()
    await expect(page).toHaveURL(/\/work\/ROBOPARK-42\?park=8/)
    await expect(page.getByRole('heading', { name: 'Задача ROBOPARK-42', exact: true })).toBeVisible()
    expect(requests).toEqual([])
  })
}
