import { expect, test } from '@playwright/test'
import { installOperational } from './fixtures'

test('overview panels consume the shared Classic card geometry', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 })
  await installOperational(page, { role: 'operator' })
  await page.goto('/overview?park=7')

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
})
