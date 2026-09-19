import { expect, test } from '@playwright/test'
import { installOperational } from './fixtures'
import { selectInterface } from '../support/interfaceMode'
import { assertResponsiveContracts } from './routeFixtures'

for (const role of ['mechanic', 'operator', 'admin', 'royal'] as const) {
  test(`${role} inventory capabilities survive interface changes`, async ({ page }) => {
    await installOperational(page, { role })
    await page.goto('/inventory?park=7')
    for (const mode of ['Новый А', 'Классический'] as const) {
      await selectInterface(page, mode)
      await page.getByRole('searchbox', { name: 'Найти запчасть' }).fill('ABC-1')
      await expect(page.getByText('Полка A-1', { exact: true })).toBeVisible()
      if (role === 'operator') {
        await expect(page.getByRole('button', { name: 'Настроить остаток' })).toHaveCount(0)
        await expect(page.getByRole('group', { name: 'Печать этикеток' })).toHaveCount(0)
        await expect(page.getByRole('tab', { name: 'Поставки' })).toHaveCount(0)
      } else {
        await expect(page.getByRole('button', { name: 'Настроить остаток' })).toBeVisible()
        await expect(page.getByRole('tab', { name: 'Выгрузка' })).toBeVisible()
        await page.evaluate(() => { window.print = () => {} })
        await page.getByRole('button', { name: 'Печатать этикетку', exact: true }).click()
        await expect(page.locator('.inventory-print-label')).toContainText('ABC-1')
        await page.getByRole('tab', { name: 'Выгрузка' }).click()
        await page.getByRole('combobox', { name: 'Формат' }).selectOption('csv')
        await page.getByRole('button', { name: 'Скачать CSV' }).click()
        await expect(page.getByRole('status')).toContainText('Файл CSV скачан')
        await page.getByRole('tab', { name: 'Запчасти' }).click()
      }
    }
  })
}

for (const width of [390, 1440]) test(`inventory A receipt draft, posting and geometry ${width}`, async ({ page }, testInfo) => {
  await page.setViewportSize({ width, height: 900 })
  await installOperational(page)
  await page.goto('/inventory?park=7&view=receipts')
  await selectInterface(page, 'Новый А')
  await page.getByRole('button', { name: 'Новая поставка' }).click()
  await page.getByRole('textbox', { name: 'Поставщик или завод' }).fill('Поставка со склада')
  await page.getByRole('searchbox', { name: 'Найти запчасть для поставки' }).fill('ABC-1')
  await page.getByRole('button', { name: 'Добавить ABC-1' }).click()
  await page.getByRole('textbox', { name: 'Количество ABC-1' }).fill('7')
  await selectInterface(page, 'Классический')
  await expect(page.getByRole('textbox', { name: 'Количество ABC-1' })).toHaveValue('7')
  await selectInterface(page, 'Новый А')
  await expect(page.getByRole('textbox', { name: 'Поставщик или завод' })).toHaveValue('Поставка со склада')
  await expect(page.locator('.inventory-document-editor')).toHaveCSS('border-radius', '12px')
  await assertResponsiveContracts(page, width)
  await page.evaluate(() => { (document.activeElement as HTMLElement)?.blur(); window.scrollTo(0, 0) })
  await page.screenshot({ path: testInfo.outputPath('receipt-a.png'), fullPage: true })
  await page.getByRole('button', { name: 'Провести поставку' }).click()
  await page.getByRole('button', { name: 'Подтвердить проведение' }).click()
  await expect(page.getByRole('status')).toContainText('Поставка проведена')
  await page.getByRole('tab', { name: 'Запчасти' }).click()
  await expect(page.getByText('7 шт.', { exact: true })).toBeVisible()
})

test('inventory A count draft survives switching and updates stock once', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 })
  await installOperational(page)
  await page.goto('/inventory?park=7&view=counts')
  await selectInterface(page, 'Новый А')
  await page.getByRole('button', { name: 'Новая инвентаризация' }).click()
  await page.getByRole('textbox', { name: 'Название акта' }).fill('Пересчёт смены')
  await page.getByRole('button', { name: 'Создать акт' }).click()
  await page.getByRole('textbox', { name: 'Фактически ABC-1' }).fill('4')
  await selectInterface(page, 'Классический')
  await expect(page.getByRole('textbox', { name: 'Фактически ABC-1' })).toHaveValue('4')
  await selectInterface(page, 'Новый А')
  await page.getByRole('button', { name: 'Провести акт' }).click()
  await page.getByRole('button', { name: 'Подтвердить проведение' }).click()
  await expect(page.getByRole('status')).toContainText('Акт проведён')
  await page.getByRole('tab', { name: 'Запчасти' }).click()
  await expect(page.getByText('4 шт.', { exact: true })).toBeVisible()
})

test('a failed receipt post is not duplicated by switching design', async ({ page }) => {
  let release!: () => void
  const pending = new Promise<void>(resolve => { release = resolve })
  let posts = 0
  await installOperational(page, { routes: [{ method: 'POST', path: /^\/api\/inventory\/parks\/7\/receipts\/\d+\/post$/, handler: async () => {
    posts++; await pending
    return { status: 409, json: { detail: { code: 'inventory_receipt_stale' } } }
  } }] })
  await page.goto('/inventory?park=7&view=receipts')
  await page.getByRole('button', { name: 'Новая поставка' }).click()
  await page.getByRole('searchbox', { name: 'Найти запчасть для поставки' }).fill('ABC-1')
  await page.getByRole('button', { name: 'Добавить ABC-1' }).click()
  await page.getByRole('button', { name: 'Провести поставку' }).click()
  const confirm = page.getByRole('button', { name: 'Подтвердить проведение' })
  await confirm.dblclick()
  await expect.poll(() => posts).toBe(1)
  // The confirmation dialog intentionally owns focus until the write settles.
  await expect(confirm).toBeDisabled()
  release()
  await expect(page.getByRole('alertdialog')).toHaveCount(0)
  await selectInterface(page, 'Новый А')
  await expect(page.getByText(/Поставка изменена другим пользователем/)).toBeVisible()
  expect(posts).toBe(1)
  await expect(page.getByText('Поставка проведена', { exact: true })).toHaveCount(0)
})
