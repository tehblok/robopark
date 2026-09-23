import { expect, test } from '@playwright/test'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'
import { installOperational, settlePage, snapshot } from './fixtures'
import { assertResponsiveContracts } from './routeFixtures'

for (const width of [390, 1440] as const) {
  test(`shared geometry tokens drive visible controls at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await installOperational(page, { role: 'mechanic' })
    await page.goto('/robots?park=7')

    const geometry = await page.evaluate(() => {
      const panel = document.createElement('section')
      panel.className = 'rp-panel'
      const button = document.createElement('button')
      button.className = 'rp-button'
      button.textContent = 'Действие'
      panel.append(button)
      document.body.append(panel)
      const root = getComputedStyle(document.documentElement)
      const result = {
        gutter: root.getPropertyValue('--rp-page-gutter').trim(),
        sectionGap: root.getPropertyValue('--rp-section-gap').trim(),
        cardPadding: root.getPropertyValue('--rp-card-padding').trim(),
        formGap: root.getPropertyValue('--rp-form-gap').trim(),
        controlRadius: root.getPropertyValue('--rp-radius-control').trim(),
        cardRadius: root.getPropertyValue('--rp-radius-card').trim(),
        buttonHeight: button.getBoundingClientRect().height,
      }
      panel.remove()
      return result
    })

    expect(geometry).toEqual({
      gutter: width <= 599 ? '12px' : '24px',
      sectionGap: width <= 599 ? '20px' : '24px',
      cardPadding: width <= 599 ? '16px' : '20px',
      formGap: width <= 599 ? '12px' : '16px',
      controlRadius: '8px',
      cardRadius: '12px',
      buttonHeight: 44,
    })
  })
}

for (const width of [599, 600] as const) {
  test(`Classic phone boundary remains stable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await installOperational(page, { role: 'royal' })
    await page.goto('/overview?park=7')
    await assertResponsiveContracts(page, width)
    const root = await page.locator('html').evaluate(element => {
      const style = getComputedStyle(element)
      return {
        gutter: style.getPropertyValue('--rp-page-gutter').trim(),
        cardPadding: style.getPropertyValue('--rp-card-padding').trim(),
      }
    })
    expect(root).toEqual(width === 599
      ? { gutter: '12px', cardPadding: '16px' }
      : { gutter: '24px', cardPadding: '20px' })
  })
}

test('overview readiness follows the current role-aware triage structure', async ({ page }) => {
  await installOperational(page, { role: 'operator' })
  await page.goto('/overview?park=7')

  await expect(page.locator('.rp-overview')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Статусы задач' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Поток задач: пришло / ушло' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Очередь внимания' })).toBeVisible()
  await expect(page.locator('.rp-insights')).toHaveCount(0)
})

test('light-theme related robot task link meets the WCAG AA contract', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('robopark-theme', 'light'))
  await installOperational(page, { role: 'operator' })
  await page.goto(`/robots/${snapshot.vin}?park=7`)

  const navigation = page.locator('.rp-check-navigation')
  await navigation.getByRole('button', { name: 'Ещё', exact: true }).click()
  await navigation.getByRole('menuitem', { name: 'Задачи', exact: true }).click()
  await expect(page.getByRole('tab', { name: 'Задачи', exact: true })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('link', { name: 'Открыть ROBOPARK-42' })).toBeVisible()
  await settlePage(page)
  await assertNoSeriousA11yViolations(page)
})
