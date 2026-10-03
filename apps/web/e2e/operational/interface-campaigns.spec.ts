import { expect, test } from '@playwright/test'
import type { CampaignDetail } from '../../src/api'
import { installOperational, settlePage, userForRole } from './fixtures'
import { assertResponsiveContracts, openRouteFixture } from './routeFixtures'

const campaign: CampaignDetail = {
  id: 4, kind: 'service_company', name: 'Осенняя СК', tracker_tag: 'замена колёс', starts_on: '2026-09-01', due_on: '2026-10-01', is_active: true,
  park_ids: [7], park_names: ['Северный парк'], total_count: 1, completed_count: 0, remaining_count: 1, pending_review_count: 0, percent_complete: 0, overdue: false,
  open_tickets: [{ key: 'RP-1', summary: 'Замена колеса', status: 'Открыт', park_id: 7, park_name: 'Северный парк', robot: '447', url: 'https://tracker.example.invalid/RP-1', completed_at: null, completed_by: null, comment: null, report_id: null, review_status: null, tracker_transition: null }], closed_tickets: [],
}

test('campaign card and creation action have a visible section gap on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 })
  await openRouteFixture(page, 'campaigns', userForRole('royal'))
  await settlePage(page)
  const card = await page.locator('.campaign-card').first().boundingBox()
  const create = await page.getByRole('button', { name: 'Новая кампания', exact: true }).boundingBox()
  expect(card && create).toBeTruthy()
  expect(create!.y - card!.y - card!.height).toBeGreaterThanOrEqual(16)
})

for (const width of [390, 1440]) test(`campaign settings and ticket lists keep a section gap at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 })
  await openRouteFixture(page, 'campaign-detail', userForRole('royal'))
  const settings = await page.locator('.campaign-detail-composition > section').boundingBox()
  const tickets = await page.locator('.campaign-detail-composition > .campaign-columns').boundingBox()
  expect(settings).not.toBeNull()
  expect(tickets).not.toBeNull()
  expect(tickets!.y - (settings!.y + settings!.height)).toBeGreaterThanOrEqual(width < 900 ? 16 : 20)
})

for (const width of [320, 390, 1440]) test(`campaign create edit and ticket draft stay stable in Classic ${width}`, async ({ page }, info) => {
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
  if (width < 600) {
    const disclosure = page.getByRole('button', { name: 'Новая кампания', exact: true })
    const target = await disclosure.boundingBox()
    expect(target?.height).toBeGreaterThanOrEqual(44)
    await expect(disclosure).toHaveCSS('border-top-width', '1px')
    await disclosure.click()
  }
  await page.getByRole('textbox', { name: 'Название', exact: true }).fill('СК сентября')
  await page.getByRole('textbox', { name: 'Часть названия тикета' }).fill('замена колёс')
  await page.getByRole('button', { name: /Парки кампании. Выбрано/ }).click()
  if (width < 600) {
    const menu = await page.getByRole('dialog', { name: 'Парки кампании' }).boundingBox()
    const navigation = await page.locator('.rp-shell__bottom-nav').boundingBox()
    expect(menu && navigation).toBeTruthy()
    expect(menu!.y + menu!.height).toBeLessThanOrEqual(navigation!.y - 8)
    if (width === 320) await page.screenshot({ path: info.outputPath('campaign-picker.png') })
  }
  await page.getByRole('checkbox', { name: 'Северный парк', exact: true }).check()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('textbox', { name: 'Название', exact: true })).toHaveValue('СК сентября')
  await page.getByRole('button', { name: 'Создать кампанию' }).click()
  await expect(page).toHaveURL(/\/campaigns\/4(?:\?park=7)?$/)
  if (width < 600) {
    const actions = await Promise.all(['Обновить из Tracker', 'Завершить кампанию', 'Удалить кампанию'].map(name => page.getByRole('button', { name }).boundingBox()))
    expect(actions.every(Boolean)).toBe(true)
    expect(Math.max(...actions.map(action => action!.width)) - Math.min(...actions.map(action => action!.width))).toBeLessThan(1)
    expect(actions[1]!.y).toBeGreaterThanOrEqual(actions[0]!.y + actions[0]!.height + 8)
  }
  if (width < 600) await page.getByRole('button', { name: 'Настройки кампании', exact: true }).click()
  await page.getByRole('textbox', { name: 'Часть названия тикета' }).fill('смена подвески')
  await expect(page.getByRole('textbox', { name: 'Часть названия тикета' })).toHaveValue('смена подвески')
  await page.getByRole('button', { name: 'Сохранить настройки кампании' }).click()
  await expect.poll(() => updates).toBe(1)
  expect(creates).toBe(1)
  await page.getByRole('button', { name: 'Заполнить и отправить на проверку' }).click()
  await page.getByRole('textbox', { name: 'Комментарий для оператора' }).fill('Выполнено')
  await expect(page.getByRole('button', { name: 'Выбрать фото' })).toBeVisible()
  await page.getByLabel('Фото', { exact: true }).setInputFiles({ name: 'robot.jpg', mimeType: 'image/jpeg', buffer: Buffer.from('photo') })
  await expect(page.getByText('robot.jpg', { exact: true })).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Комментарий для оператора' })).toHaveValue('Выполнено')
  expect(await page.getByLabel('Фото', { exact: true }).evaluate((input: HTMLInputElement) => input.files?.[0]?.name)).toBe('robot.jpg')
  if (width < 600) {
    const [submit, cancel] = await Promise.all([
      page.getByRole('button', { name: 'Отправить оператору' }).boundingBox(),
      page.getByRole('button', { name: 'Отмена', exact: true }).boundingBox(),
    ])
    expect(submit && cancel).toBeTruthy()
    expect(Math.abs(submit!.width - cancel!.width)).toBeLessThan(1)
    expect(cancel!.y).toBeGreaterThanOrEqual(submit!.y + submit!.height + 8)
  }
  await expect(page.locator('.campaign-columns')).toHaveCSS('gap', width < 900 ? '16px' : '20px')
  await assertResponsiveContracts(page, width)
  await page.evaluate(() => { (document.activeElement as HTMLElement)?.blur(); window.scrollTo(0, 0) })
  await page.screenshot({ path: info.outputPath('campaign-classic.png'), fullPage: true })
})
