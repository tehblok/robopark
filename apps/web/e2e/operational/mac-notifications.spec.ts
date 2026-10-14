import { expect, test } from '@playwright/test'
import { installOperational, userForRole } from './fixtures'

test('royal can open announcements even while the schedule is unavailable', async ({ page }) => {
  let announcements = 0
  await installOperational(page, { user: userForRole('royal'), realTime: true, routes: [
    { method: 'GET', path: '/api/schedules', handler: () => ({ status: 503, json: { detail: 'unavailable' } }) },
    { method: 'GET', path: '/api/schedules/participants', handler: () => ({ json: [] }) },
    { method: 'GET', path: '/api/push/inbox', handler: () => ({ json: [{ id: 'maintenance1', event_type: 'maintenance', park_id: null, protected_text: 'Технические работы с 23:00 до 23:15', read_at: null, created_at: new Date().toISOString() }] }) },
    { method: 'POST', path: '/api/push/announcements', handler: async request => {
      announcements++
      const body = await request.json()
      return { json: { ...body, event_id: 'event1', action: null, counters: { inbox_created: 12, push_eligible: 8 } } }
    } },
  ] })
  await page.goto('/schedule?park=7')
  await page.getByRole('button', { name: 'Уведомления', exact: true }).click()
  await expect(page.locator('#notifications')).toBeVisible()
  await expect(page.getByText('Технические работы с 23:00 до 23:15')).toBeVisible()
  await page.getByLabel('Тип сообщения').selectOption('action_required')
  await page.getByLabel('Текст сообщения').fill('Сохраните результаты перед техническими работами.')
  expect(announcements).toBe(0)
  await page.getByRole('button', { name: 'Отправить сотрудникам' }).click()
  await expect(page.getByText('Объявление принято сервером. Новых сообщений: 12.')).toBeVisible()
  expect(announcements).toBe(1)
  await page.screenshot({ path: '../../artifacts/0.2.3-rc.2/verification/royal-notifications.png', fullPage: true })
})

test('operator sees working events without royal broadcast controls', async ({ page }) => {
  await installOperational(page, { user: userForRole('operator'), realTime: true, routes: [
    { method: 'GET', path: '/api/schedules', handler: () => ({ json: [] }) },
    { method: 'GET', path: '/api/push/inbox', handler: () => ({ json: [{ id: 'report1', event_type: 'report', park_id: 7, protected_text: 'Поступил новый репорт о роботе', read_at: null, created_at: new Date().toISOString() }] }) },
  ] })
  await page.goto('/schedule?park=7#notifications')
  await expect(page.getByText('Поступил новый репорт о роботе')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Отправить сотрудникам' })).toHaveCount(0)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({ path: '../../artifacts/0.2.3-rc.2/verification/operator-notifications-phone.png', fullPage: true })
})
