import { expect, test } from '@playwright/test'
import { openRouteFixture } from './routeFixtures'
import { userForRole } from './fixtures'

test('park selector uses the shared button and fits an open menu on a narrow phone', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 720 })
  await openRouteFixture(page, 'admin-users', userForRole('royal'))
  const trigger = page.locator('.park-multi-select__trigger').first()
  await expect(trigger).toBeVisible()
  await expect(trigger).toHaveClass(/rp-button--secondary/)
  const triggerBox = await trigger.boundingBox()
  expect(triggerBox?.height).toBeGreaterThanOrEqual(44)

  await trigger.click()
  const menu = page.getByRole('dialog', { name: 'Парки' }).first()
  await expect(menu).toBeVisible()
  const menuBox = await menu.boundingBox()
  expect(menuBox).not.toBeNull()
  expect(menuBox!.x).toBeGreaterThanOrEqual(0)
  expect(menuBox!.x + menuBox!.width).toBeLessThanOrEqual(320)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320)
  await menu.getByRole('checkbox', { name: 'Южный парк' }).check()
  await expect(trigger).toContainText('Выбрано: 2')
  await page.screenshot({ path: '/tmp/robopark-admin-user-park-picker-320.png', fullPage: true })
})
