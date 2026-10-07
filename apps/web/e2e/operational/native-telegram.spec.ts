import { expect, test } from '@playwright/test'
import type { BotJob, NativeTelegramState } from '../../src/domains/telegram/nativeTelegramApi'
import { installOperational, parkNorth, parkSouth, settlePage } from './fixtures'

test.use({ trace: 'off' })

test('admin configures native Telegram on desktop and opens a private link code on mobile', async ({ page }, testInfo) => {
  const parks = [parkNorth, parkSouth].map(park => ({
    id: park.id,
    name: park.name,
    tag: park.tag,
    timezone: park.timezone,
    chat_id: null,
    thread_id: null,
    revision: 1,
  }))
  const state: NativeTelegramState = {
    parks,
    jobs: [],
    deliveries: [],
    health: {
      state: 'ready', telegram_ok: true, scheduler_ok: true,
      updated_at: '2026-09-02T09:04:45Z', last_error: null,
    },
  }
  let savedPark: Record<string, unknown> | null = null
  let createdJob: BotJob | null = null

  await page.setViewportSize({ width: 1440, height: 900 })
  await installOperational(page, { role: 'admin', routes: [
    { method: 'GET', path: '/api/admin/bot/native', handler: () => ({ json: state }) },
    { method: 'PUT', path: '/api/admin/bot/native/parks/7', handler: async request => {
      savedPark = await request.json() as Record<string, unknown>
      state.parks[0] = { ...state.parks[0], chat_id: Number(savedPark.chat_id), thread_id: Number(savedPark.thread_id), revision: 2 }
      return { json: state.parks[0] }
    } },
    { method: 'POST', path: '/api/admin/bot/native/jobs', handler: async request => {
      const input = await request.json() as Omit<BotJob, 'id' | 'revision'>
      createdJob = { ...input, id: '0199b6bb-3e56-7c31-8c03-468949d63fd1', revision: 1 }
      state.jobs.push(createdJob)
      return { status: 201, json: createdJob }
    } },
    { method: 'GET', path: '/api/bot/account', handler: () => ({ json: { linked: false, telegram_user_id: null } }) },
    { method: 'POST', path: '/api/bot/account/link-code', handler: () => ({
      json: { code: '48214821', expires_at: '2026-09-02T09:15:00Z' },
    }) },
  ] })

  await page.goto('/admin/settings?park=7&tab=telegram')
  await expect(page.getByRole('heading', { name: 'Состояние сервиса' })).toBeVisible()
  await expect(page.getByText('Telegram отвечает')).toBeVisible()

  await page.getByLabel('ID чата').fill('-1001234567890')
  await page.getByLabel('ID темы').fill('42')
  await page.getByRole('button', { name: 'Сохранить чат' }).click()
  await expect(page.getByText('Чат и тема сохранены.')).toBeVisible()
  expect(savedPark).toMatchObject({ chat_id: -1001234567890, thread_id: 42, revision: 1 })

  await page.getByRole('button', { name: 'Новое задание' }).click()
  await page.getByLabel('Тип задания').selectOption('text')
  await page.getByLabel('Название').fill('Памятка начала смены')
  await page.getByRole('textbox', { name: 'Текст', exact: true }).fill('Проверьте заряд роботов перед выездом.')
  await expect(page.getByRole('checkbox', { name: 'Задание включено' })).not.toBeChecked()
  await page.getByRole('button', { name: 'Создать задание' }).click()
  await expect(page.getByText('Задание создано.')).toBeVisible()
  await expect(page.getByText('Памятка начала смены')).toBeVisible()
  await expect(page.getByText('Текст · В заданное время: 09:00 · Пн–Вс · время парка · выключено')).toBeVisible()
  expect(createdJob).toMatchObject({ kind: 'text', enabled: false, park_id: 7 })
  await settlePage(page)
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: testInfo.outputPath('native-telegram-desktop.png'), fullPage: true, animations: 'disabled' })

  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByRole('button', { name: /^(?:Ещё|Меню)$/ }).click()
  const menu = page.getByRole('dialog', { name: 'Меню' })
  await menu.getByRole('button', { name: 'Telegram' }).click()
  const account = page.getByRole('dialog', { name: 'Telegram' })
  await expect(account.getByText('Telegram не привязан')).toBeVisible()
  await account.getByRole('button', { name: 'Получить код привязки' }).click()
  await expect(account.getByRole('status')).toContainText('/link 48214821')
  await expect(account.getByRole('status')).toContainText('в личном чате')
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await settlePage(page)
  await page.screenshot({ path: testInfo.outputPath('native-telegram-mobile.png'), fullPage: false, animations: 'disabled' })
})
