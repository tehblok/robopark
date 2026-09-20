import { expect, test } from '@playwright/test'
import { installOperational, roles } from './fixtures'
import { selectInterface } from '../support/interfaceMode'
import { assertResponsiveContracts } from './routeFixtures'

const action = { mechanic: 'Мои задачи', operator: 'Приёмка ремонта', driver: 'Найти робота', admin: 'Входящие репорты', royal: 'Входящие репорты' }
for (const role of roles) test(`${role} overview A offers scoped next actions and keeps filters`, async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 900 })
  await installOperational(page, { role })
  await page.goto('/overview?park=7')
  await selectInterface(page, 'Новый А')
  const actions = page.getByRole('navigation', { name: 'Действия смены' })
  await expect(actions.getByRole('link', { name: action[role], exact: true })).toBeVisible()
  for (const link of await actions.getByRole('link').all()) await expect(link).toHaveAttribute('href', /park=7/)
  const task = page.getByRole('link', { name: 'Открыть задачу ROBOPARK-42' })
  await expect(task).toBeVisible()
  await assertResponsiveContracts(page, 390)
  await page.evaluate(() => { (document.activeElement as HTMLElement)?.blur(); window.scrollTo(0, 0) })
  await page.screenshot({ path: info.outputPath('overview-a.png'), fullPage: true })
  await task.click()
  await page.goBack()
  await expect(page).toHaveURL('/overview?park=7')
  await expect(actions).toBeVisible()
  await selectInterface(page, 'Классический')
  await expect(actions).toHaveCount(0)
})

test('overview panels consume the shared card geometry in both interfaces', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 })
  await installOperational(page, { role: 'operator' })
  await page.goto('/overview?park=7')

  for (const mode of ['Классический', 'Новый А'] as const) {
    await selectInterface(page, mode)
    const geometry = await page.locator('.rp-overview-alert').first().evaluate(element => {
      const style = getComputedStyle(element)
      const root = getComputedStyle(document.documentElement)
      return {
        paddingInline: style.paddingInlineStart,
        expectedPadding: root.getPropertyValue('--rp-card-padding').trim(),
        radius: style.borderRadius,
        expectedRadius: root.getPropertyValue('--rp-radius-card').trim(),
      }
    })
    expect(geometry.paddingInline).toBe(geometry.expectedPadding)
    expect(geometry.radius).toBe(geometry.expectedRadius)
  }
})
