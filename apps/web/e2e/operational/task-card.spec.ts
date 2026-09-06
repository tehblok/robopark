import { expect, test } from '@playwright/test'
import type { TrackerComment } from '../../src/api'
import { FIXED_TIME, installOperational, issue, settlePage } from './fixtures'

test('320px task controls, safe Markdown, persistent drafts and incoming comment navigation', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 320, height: 900 })
  const comments: TrackerComment[] = [{ id: 'old', text: '**Начальная** запись', author_login: 'operator.test', created_at: FIXED_TIME }]
  const sent: string[] = []
  await installOperational(page, { issue: { ...issue,
    description: `**Важно** и *проверено*\n\n- Крепление\n- Питание\n\n[Инструкция](https://example.org)\n\n<script>alert(1)</script>\n\n${'Длинное описание. '.repeat(100)}Конец описания`,
  }, routes: [
    { method: 'GET', path: '/api/tracker/issues/ROBOPARK-42/comments', handler: () => ({ json: comments }) },
    { method: 'POST', path: '/api/tracker/issues/ROBOPARK-42/comment', handler: async request => {
      const { text } = await request.json() as { text: string }
      sent.push(text)
      comments.push({ id: 'own', text, author_login: 'mechanic.test', created_at: FIXED_TIME })
      return { json: { key: issue.key, action: 'comment', status: 'ok', actor: 'mechanic.test', performed_at: FIXED_TIME } }
    } },
  ] })
  await page.goto('/work/ROBOPARK-42?park=7')
  await expect(page.locator('.issue-actions')).toBeVisible()
  await settlePage(page)
  await expect(page.locator('.issue-rich-text strong').first()).toHaveText('Важно')
  await expect(page.locator('.issue-rich-text script')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Показать описание полностью' })).toHaveAttribute('aria-expanded', 'false')
  await page.getByRole('button', { name: 'Показать описание полностью' }).click()
  await expect(page.getByText(/Конец описания/)).toBeVisible()
  await page.getByRole('button', { name: 'Свернуть описание' }).click()
  const controls = [
    page.getByRole('link', { name: 'Открыть в Startrek', exact: true }),
    page.getByRole('button', { name: 'Закрыть тикет', exact: true }),
    page.getByRole('button', { name: 'Назначить', exact: true }),
    page.getByRole('button', { name: 'Отправить', exact: true }),
  ]
  for (const control of controls) {
    const box = await control.boundingBox()
    expect(box?.width).toBeGreaterThan(180)
    expect(box?.height).toBeGreaterThanOrEqual(44)
    expect(box?.height).toBeLessThan(100)
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.locator('.issue-action-footer').scrollIntoViewIfNeeded()
  await page.screenshot({ path: testInfo.outputPath('task-controls-320.png') })

  const composer = page.getByRole('textbox', { name: 'Комментарии', exact: true })
  await composer.fill('Черновик после перезагрузки')
  await page.reload()
  await expect(composer).toHaveValue('Черновик после перезагрузки')
  await page.getByRole('tab', { name: 'Открытые задачи', exact: true }).click()
  await page.getByRole('tab', { name: 'Задача', exact: true }).click()
  await expect(composer).toHaveValue('Черновик после перезагрузки')

  await page.evaluate(() => window.scrollTo(0, 0))
  const scrollBefore = await page.evaluate(() => scrollY)
  comments.push({ id: 'incoming', text: '**Новый ответ коллеги**', author_login: 'operator.test', created_at: '2026-09-02T09:06:00Z' })
  await page.clock.setFixedTime(new Date('2026-09-02T09:06:01Z'))
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  const jump = page.getByRole('button', { name: '1 новый комментарий — перейти' })
  await expect(jump).toBeVisible()
  expect(await page.evaluate(() => scrollY)).toBe(scrollBefore)
  await jump.click()
  await expect(page.locator('[data-comment-id="incoming"]')).toBeFocused()
  await expect(page.locator('[data-comment-id="incoming"] strong')).toHaveText('Новый ответ коллеги')
  await expect(composer).toHaveValue('Черновик после перезагрузки')
  await page.getByRole('button', { name: 'Отправить', exact: true }).click()
  await expect(composer).toHaveValue('')
  expect(sent).toEqual(['Черновик после перезагрузки'])
  await expect(page.getByRole('button', { name: /новый комментарий — перейти/ })).toHaveCount(0)
  await page.reload()
  await expect(composer).toHaveValue('')
})

test('robot repair descriptions keep classification, comment, zone and repair notes only', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 })
  await installOperational(page, { issue: { ...issue, description: `**SUF**: робот находится под управлением системы SUF
**Classificator:** Disk space
**Comment:** Недостаточно места на диске
**Zone:** Lavka Smolensky
**Port:** Moscow Robot
**Rover name:** a1217
**Mode:** AUTO_MODE_AUTO
Что было сделано: Агрессивная чистка, почищены старые докер-образы. Рекомендации: Забит логами, необходимо слить по шнурку.` } })
  await page.goto('/work/ROBOPARK-42?park=7')
  const description = page.locator('.issue-section').filter({ has: page.getByRole('heading', { name: 'Описание', exact: true }) })
  await expect(description).toContainText('Classificator: Disk space')
  await expect(description).toContainText('Comment: Недостаточно места на диске')
  await expect(description).toContainText('Zone: Lavka Smolensky')
  await expect(description).toContainText('Что было сделано: Агрессивная чистка, почищены старые докер-образы.')
  await expect(description).toContainText('Рекомендации: Забит логами, необходимо слить по шнурку.')
  await expect(description).not.toContainText('SUF')
  await expect(description).not.toContainText('Port:')
  await expect(description).not.toContainText('AUTO_MODE_AUTO')
  await expect(description.getByRole('button')).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})
