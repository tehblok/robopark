import { expect, test } from '@playwright/test'
import { FIXED_TIME, installOperational, issue } from './fixtures'

test('handoff persists and an uncertain comment retains its request key after reload', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 })
  await page.addInitScript(() => { Math.random = () => 0 })
  const requests: string[] = []
  let handoff = { revision: 0, done: '', remaining: '', obstacles: '', author: null as string | null, updated_at: null as string | null }
  await installOperational(page, { role: 'mechanic', routes: [
    { method: 'POST', path: '/api/tracker/issues/ROBOPARK-42/presence', handler: () => ({ json: { people: [{ username: 'Коллега' }] } }) },
    { method: 'GET', path: '/api/tracker/issues/ROBOPARK-42/handoff', handler: () => ({ json: handoff }) },
    { method: 'PUT', path: '/api/tracker/issues/ROBOPARK-42/handoff', handler: async request => {
      handoff = { ...await request.json(), revision: handoff.revision + 1, author: 'mechanic.test', updated_at: FIXED_TIME }
      return { json: handoff }
    } },
    { method: 'POST', path: '/api/tracker/issues/ROBOPARK-42/comment', handler: request => {
      requests.push(request.headers.get('Idempotency-Key') ?? '')
      expect(JSON.parse(decodeURIComponent(request.headers.get('X-Tracker-State') ?? ''))).toEqual({ status: issue.status, status_key: issue.status_key, assignee: issue.assignee?.login })
      return { status: 409, json: { detail: 'tracker_submission_uncertain' } }
    } },
  ] })
  await page.goto('/work/ROBOPARK-42?park=7')
  await expect(page.getByText(/Сейчас в задаче: Коллега/)).toBeVisible()
  await page.getByText('Передача смены', { exact: true }).click()
  await page.getByRole('textbox', { name: 'Сделано', exact: true }).fill('Заменено колесо')
  await page.getByRole('textbox', { name: 'Осталось', exact: true }).fill('Проверить на маршруте')
  await page.getByRole('textbox', { name: 'Препятствия', exact: true }).fill('Нет заряженной батареи')
  await page.getByRole('button', { name: 'Сохранить передачу смены' }).click()
  await expect.poll(() => handoff.revision).toBe(1)
  const composer = page.getByRole('textbox', { name: 'Комментарии', exact: true })
  await composer.fill('Передаю следующей смене')
  await page.getByRole('button', { name: 'Отправить', exact: true }).click()
  await expect(page.getByText(/Результат отправки пока неизвестен/)).toBeVisible()
  await page.reload()
  await expect(composer).toHaveValue('Передаю следующей смене')
  await page.getByText('Передача смены', { exact: true }).click()
  await expect(page.getByRole('textbox', { name: 'Осталось', exact: true })).toHaveValue('Проверить на маршруте')
  await page.getByRole('button', { name: 'Отправить', exact: true }).click()
  await expect.poll(() => requests.length).toBe(2)
  expect(requests[0]).not.toBe('')
  expect(requests[1]).toBe(requests[0])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})
