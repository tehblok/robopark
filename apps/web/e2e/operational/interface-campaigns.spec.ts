import { expect, test } from '@playwright/test'
import type { CampaignDetail } from '../../src/api'
import { installOperational } from './fixtures'
import { selectInterface } from '../support/interfaceMode'
import { assertResponsiveContracts } from './routeFixtures'

const campaign: CampaignDetail = {
  id: 4, kind: 'service_company', name: 'Осенняя СК', tracker_tag: 'замена колёс', starts_on: '2026-09-01', due_on: '2026-10-01', is_active: true,
  park_ids: [7], park_names: ['Северный парк'], total_count: 1, completed_count: 0, remaining_count: 1, pending_review_count: 0, percent_complete: 0, overdue: false,
  open_tickets: [{ key: 'RP-1', summary: 'Замена колеса', status: 'Открыт', park_id: 7, park_name: 'Северный парк', robot: '447', url: 'https://tracker.example.invalid/RP-1', completed_at: null, completed_by: null, comment: null, report_id: null, review_status: null, tracker_transition: null }], closed_tickets: [],
}

for (const width of [390, 1440]) test(`campaign create edit and ticket draft survive design change ${width}`, async ({ page }, info) => {
  let value = { ...campaign }
  let creates = 0
  let updates = 0
  await page.setViewportSize({ width, height: 900 })
  await installOperational(page, { role: 'royal', routes: [
    { method: 'GET', path: '/api/campaigns', handler: () => ({ json: [] }) },
    { method: 'GET', path: '/api/campaigns/4', handler: () => ({ json: value }) },
    { method: 'POST', path: '/api/campaigns', handler: async request => { creates++; value = { ...value, ...await request.json() }; return { json: value } } },
    { method: 'PATCH', path: '/api/campaigns/4', handler: async request => { updates++; value = { ...value, ...await request.json() }; return { json: value } } },
  ] })
  await page.goto('/campaigns?park=7')
  await selectInterface(page, 'Новый А')
  if (width < 600) await page.getByRole('button', { name: 'Новая кампания', exact: true }).click()
  await page.getByRole('textbox', { name: 'Название', exact: true }).fill('СК сентября')
  await page.getByRole('textbox', { name: 'Часть названия тикета' }).fill('замена колёс')
  await page.getByRole('button', { name: /Парки кампании. Выбрано/ }).click()
  await page.getByRole('checkbox', { name: 'Северный парк', exact: true }).check()
  await page.keyboard.press('Escape')
  await selectInterface(page, 'Классический')
  await expect(page.getByRole('textbox', { name: 'Название', exact: true })).toHaveValue('СК сентября')
  await page.getByRole('button', { name: 'Создать кампанию' }).click()
  await expect(page).toHaveURL('/campaigns/4')
  await selectInterface(page, 'Новый А')
  if (width < 600) await page.getByRole('button', { name: 'Настройки кампании', exact: true }).click()
  await page.getByRole('textbox', { name: 'Часть названия тикета' }).fill('смена подвески')
  await selectInterface(page, 'Классический')
  await expect(page.getByRole('textbox', { name: 'Часть названия тикета' })).toHaveValue('смена подвески')
  await selectInterface(page, 'Новый А')
  await page.getByRole('button', { name: 'Сохранить настройки кампании' }).click()
  await expect.poll(() => updates).toBe(1)
  expect(creates).toBe(1)
  await page.getByRole('button', { name: 'Заполнить и отправить на проверку' }).click()
  await page.getByRole('textbox', { name: 'Комментарий для оператора' }).fill('Выполнено')
  await page.getByLabel('Фото', { exact: true }).setInputFiles({ name: 'robot.jpg', mimeType: 'image/jpeg', buffer: Buffer.from('photo') })
  await selectInterface(page, 'Классический')
  await selectInterface(page, 'Новый А')
  await expect(page.getByRole('textbox', { name: 'Комментарий для оператора' })).toHaveValue('Выполнено')
  expect(await page.getByLabel('Фото', { exact: true }).evaluate((input: HTMLInputElement) => input.files?.[0]?.name)).toBe('robot.jpg')
  await expect(page.locator('.campaign-columns')).toHaveCSS('gap', '24px')
  await assertResponsiveContracts(page, width)
  await page.evaluate(() => { (document.activeElement as HTMLElement)?.blur(); window.scrollTo(0, 0) })
  await page.screenshot({ path: info.outputPath('campaign-a.png'), fullPage: true })
})
