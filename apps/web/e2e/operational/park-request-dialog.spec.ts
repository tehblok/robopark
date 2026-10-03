import { expect, test } from '@playwright/test'
import { openRouteFixture } from './routeFixtures'
import { userForRole } from './fixtures'

test('operator park request stays inside an accessible mobile dialog', async ({ page }, info) => {
  await page.setViewportSize({ width: 320, height: 720 })
  await openRouteFixture(page, 'operator-parks', userForRole('operator'))
  const trigger = page.locator('section.panel').filter({ has: page.getByRole('heading', { name: 'Запросить парк', exact: true, level: 2 }) })
    .getByRole('button', { name: 'Запросить парк', exact: true })
  await trigger.click()

  const dialog = page.getByRole('dialog', { name: 'Запросить парк' })
  await expect(dialog).toBeVisible()
  await expect(page.locator('#root')).toHaveAttribute('inert', '')
  await expect.poll(() => page.evaluate(() => document.body.style.overflow)).toBe('hidden')
  await expect(dialog.getByLabel('Парк', { exact: true })).toHaveValue('8')
  const box = await dialog.boundingBox()
  expect(box).not.toBeNull()
  expect(box!.x).toBeGreaterThanOrEqual(0)
  expect(box!.x + box!.width).toBeLessThanOrEqual(320)

  const actions = dialog.locator('.form-actions button')
  const first = await actions.nth(0).boundingBox()
  const second = await actions.nth(1).boundingBox()
  expect(first).not.toBeNull()
  expect(second).not.toBeNull()
  expect(Math.abs(first!.width - second!.width)).toBeLessThanOrEqual(1)
  const gap = first!.x + first!.width <= second!.x
    ? second!.x - first!.x - first!.width
    : second!.y - first!.y - first!.height
  expect(gap).toBeGreaterThanOrEqual(8)

  await page.screenshot({ path: info.outputPath('park-request-dialog-320.png'), animations: 'disabled' })
  await dialog.getByRole('button', { name: 'Закрыть', exact: true }).last().focus()
  await page.keyboard.press('Tab')
  await expect(dialog.getByLabel('Парк', { exact: true })).toBeFocused()

  await dialog.getByRole('button', { name: 'Закрыть', exact: true }).last().click()
  await expect(dialog).toBeHidden()
  await expect(page.locator('#root')).not.toHaveAttribute('inert')
  await expect(trigger).toBeFocused()
})

test('operator can still submit a park request from the shared dialog', async ({ page }) => {
  const submitted: number[] = []
  await page.setViewportSize({ width: 390, height: 844 })
  await openRouteFixture(page, 'operator-parks', userForRole('operator'), { routes: [
    { method: 'POST', path: '/api/operator/park-requests', handler: async request => {
      submitted.push(Number(((await request.json()) as { park_id: number }).park_id))
      return { json: { id: 12, park_id: 8, user_id: 101, status: 'pending', created_at: '2026-09-02T09:00:00Z', resolved_at: null, resolved_by: null } }
    } },
  ] })
  const trigger = page.locator('section.panel').filter({ has: page.getByRole('heading', { name: 'Запросить парк', exact: true, level: 2 }) })
    .getByRole('button', { name: 'Запросить парк', exact: true })
  await trigger.click()
  const dialog = page.getByRole('dialog', { name: 'Запросить парк' })
  await dialog.getByRole('button', { name: 'Отправить заявку' }).click()
  await expect.poll(() => submitted).toEqual([8])
  await expect(dialog.getByText('Заявка отправлена администратору.')).toBeVisible()
  await expect(dialog).toBeHidden()
  await expect(trigger).toBeFocused()
})

test('operator park request dialog remains compact on desktop', async ({ page }, info) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openRouteFixture(page, 'operator-parks', userForRole('operator'))
  await page.getByRole('button', { name: 'Запросить парк', exact: true }).first().click()
  const dialog = page.getByRole('dialog', { name: 'Запросить парк' })
  const box = await dialog.boundingBox()
  expect(box).not.toBeNull()
  expect(box!.width).toBeLessThanOrEqual(576)
  expect(Math.abs(box!.x + box!.width / 2 - 720)).toBeLessThanOrEqual(1)
  const select = await dialog.getByLabel('Парк', { exact: true }).boundingBox()
  expect(select).not.toBeNull()
  expect(select!.width).toBeGreaterThan(box!.width * .85)
  await page.screenshot({ path: info.outputPath('park-request-dialog-1440.png'), animations: 'disabled' })
})

test('an earlier successful request cannot close a newly reopened dialog', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openRouteFixture(page, 'operator-parks', userForRole('operator'), { routes: [
    { method: 'POST', path: '/api/operator/park-requests', handler: () => ({ json: { id: 12, park_id: 8, user_id: 101, status: 'pending', created_at: '2026-09-02T09:00:00Z', resolved_at: null, resolved_by: null } }) },
  ] })
  const trigger = page.locator('section.panel').filter({ has: page.getByRole('heading', { name: 'Запросить парк', exact: true, level: 2 }) })
    .getByRole('button', { name: 'Запросить парк', exact: true })
  await trigger.click()
  const dialog = page.getByRole('dialog', { name: 'Запросить парк' })
  await dialog.getByRole('button', { name: 'Отправить заявку' }).click()
  await expect(dialog.getByText('Заявка отправлена администратору.')).toBeVisible()
  await dialog.getByRole('button', { name: 'Закрыть', exact: true }).first().click()
  await trigger.click()
  await expect(dialog).toBeVisible()
  await page.waitForTimeout(1000)
  await expect(dialog).toBeVisible()
})
