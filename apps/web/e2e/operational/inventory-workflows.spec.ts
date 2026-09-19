import { expect, test, type Page } from '@playwright/test'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'
import { installOperational, parkNorth, userForRole, type OperationalRole } from './fixtures'
import { assertResponsiveContracts } from './routeFixtures'

async function openAs(page: Page, role: OperationalRole, path: string, parks = userForRole(role).parks) {
  await installOperational(page, { role, user: { ...userForRole(role), parks }, parks })
  await page.goto(path)
  await expect(page.getByRole('heading', { name: 'Склад', exact: true })).toBeVisible()
}

async function assertInventoryPage(page: Page, width: number) {
  await assertResponsiveContracts(page, width)
  await assertNoSeriousA11yViolations(page)
}

async function assertOpenDocumentEditor(page: Page, width: number) {
  const editor = page.locator('.inventory-document-editor:visible')
  const list = page.locator('.inventory-document-list')
  await expect(editor).toHaveCount(1)
  if (width < 600) await expect(list).toBeHidden()
  else {
    await expect(list).toBeVisible()
    const [listBox, editorBox] = await Promise.all([list.boundingBox(), editor.boundingBox()])
    expect(listBox && editorBox).toBeTruthy()
    expect(listBox!.y + listBox!.height).toBeLessThanOrEqual(editorBox!.y)
  }
  await assertInventoryPage(page, width)
}

async function postReceipt(page: Page, article: string, quantity: string, width: number) {
  await page.getByRole('tab', { name: 'Поставки' }).click()
  await page.getByRole('button', { name: 'Новая поставка' }).click()
  await assertOpenDocumentEditor(page, width)
  await page.getByRole('searchbox', { name: 'Найти запчасть для поставки' }).fill(article)
  await page.getByRole('button', { name: `Добавить ${article}` }).click()
  await page.getByRole('textbox', { name: `Количество ${article}` }).fill(quantity)
  await page.getByRole('button', { name: 'Провести поставку' }).click()
  const dialog = page.getByRole('alertdialog', { name: 'Подтвердите действие' })
  await expect(dialog).toBeVisible()
  await assertOpenDocumentEditor(page, width)
  await dialog.getByRole('button', { name: 'Подтвердить проведение' }).click()
  await expect(page.getByRole('status')).toContainText('Поставка проведена')
}

async function postCount(page: Page, article: string, quantity: string, width: number) {
  await page.getByRole('tab', { name: 'Инвентаризация' }).click()
  await page.getByRole('button', { name: 'Новая инвентаризация' }).click()
  await assertOpenDocumentEditor(page, width)
  await page.getByRole('textbox', { name: 'Название акта' }).fill('Контрольный пересчёт')
  await page.getByRole('button', { name: 'Создать акт' }).click()
  await page.getByRole('textbox', { name: `Фактически ${article}` }).fill(quantity)
  await page.getByRole('button', { name: 'Провести акт' }).click()
  const dialog = page.getByRole('alertdialog', { name: 'Подтвердите действие' })
  await expect(dialog).toBeVisible()
  await assertOpenDocumentEditor(page, width)
  await dialog.getByRole('button', { name: 'Подтвердить проведение' }).click()
  await expect(page.getByRole('status')).toContainText('Акт проведён')
}

test('mechanic completes the park stock cycle on phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openAs(page, 'mechanic', '/inventory?park=7')
  await page.getByRole('searchbox', { name: 'Найти запчасть' }).fill('ABC-1')
  await expect(page.getByText('Полка A-1', { exact: true })).toBeVisible()
  await postReceipt(page, 'ABC-1', '5', 390)
  await page.getByRole('tab', { name: 'Запчасти' }).click()
  await expect(page.getByText('5 шт.', { exact: true })).toBeVisible()
  const duplicateStatus = await page.evaluate(async () => (await fetch('/api/inventory/parks/7/receipts/1/post', { method: 'POST' })).status)
  expect(duplicateStatus).toBe(200)
  await page.reload()
  await page.getByRole('tab', { name: 'Запчасти' }).click()
  await expect(page.getByText('5 шт.', { exact: true })).toBeVisible()
  await postCount(page, 'ABC-1', '4', 390)
  await page.getByRole('tab', { name: 'Запчасти' }).click()
  await expect(page.getByText('4 шт.', { exact: true })).toBeVisible()
  await assertInventoryPage(page, 390)
})

test('mechanic cannot broaden inventory to a foreign park at 320px', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 844 })
  await openAs(page, 'mechanic', '/inventory?park=8', [parkNorth])
  await expect(page).toHaveURL(/\/inventory\?park=7(?:&|$)/)
  await expect(page.getByText('Учёт запчастей парка «Северный парк»', { exact: true })).toBeVisible()
  await expect(page.getByText('Южный парк', { exact: true })).toHaveCount(0)
  const stickySearch = page.locator('.inventory-catalog-view > .rp-form-field').first()
  await expect(stickySearch).toHaveCSS('position', 'sticky')
  expect(await stickySearch.evaluate(element => element.closest('main') !== null)).toBe(true)
  await assertInventoryPage(page, 320)
  await postCount(page, 'ABC-1', '0', 320)
})

test('mechanic switches inventory workflows with the keyboard at 768px', async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 900 })
  await openAs(page, 'mechanic', '/inventory?park=7')
  const parts = page.getByRole('tab', { name: 'Запчасти' })
  await parts.focus()
  await parts.press('ArrowRight')
  await expect(page.getByRole('tab', { name: 'Поставки' })).toHaveAttribute('aria-selected', 'true')
  await expect(page).toHaveURL(/view=receipts/)
  await expect(page.getByRole('heading', { name: 'Поставки', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Новая поставка' }).click()
  await assertOpenDocumentEditor(page, 768)
})

test('stock settings remain isolated between parks', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 900 })
  await openAs(page, 'admin', '/inventory?park=7')
  await page.getByRole('button', { name: 'Настроить остаток' }).click()
  await page.getByRole('textbox', { name: 'Место' }).fill('Полка N-7')
  await page.getByRole('textbox', { name: 'Минимум' }).fill('9')
  await page.getByRole('button', { name: 'Сохранить' }).click()
  await expect(page.getByText('Полка N-7', { exact: true })).toBeVisible()
  await page.goto('/inventory?park=8')
  await expect(page.getByText('Полка A-1', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Настроить остаток' }).click()
  await expect(page.getByRole('textbox', { name: 'Минимум' })).toHaveValue('2')
})

test('admin opens a global catalog workflow at 1024px', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 900 })
  await openAs(page, 'admin', '/inventory?park=7&view=manage')
  await expect(page.getByRole('heading', { name: 'Глобальный каталог', exact: true })).toBeVisible()
  await page.getByRole('combobox', { name: 'Позиция каталога' }).selectOption('101')
  await page.getByRole('button', { name: 'Добавить позицию' }).click()
  await expect(page.getByRole('form', { name: 'Новая позиция' })).toBeVisible()
  await assertInventoryPage(page, 1024)
})

test('royal exports all parks at 1440px', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  let exportUrl = ''
  await openAs(page, 'royal', '/inventory?park=7&view=export')
  page.on('request', request => {
    if (new URL(request.url()).pathname === '/api/inventory/export') exportUrl = request.url()
  })
  await page.getByRole('combobox', { name: 'Охват выгрузки' }).selectOption('all')
  await page.getByRole('combobox', { name: 'Формат' }).selectOption('csv')
  await page.getByRole('button', { name: 'Скачать CSV' }).click()
  await expect(page.getByRole('status')).toContainText('Файл CSV скачан')
  expect(exportUrl).toContain('scope=all')
  expect(exportUrl).toContain('format=csv')
  await assertInventoryPage(page, 1440)
})

test('large receipt workflow keeps list and editor visible side by side', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openAs(page, 'mechanic', '/inventory?park=7&view=receipts')
  await page.getByRole('button', { name: 'Новая поставка' }).click()
  const list = page.locator('.inventory-document-list')
  const editor = page.locator('.inventory-document-editor')
  await expect(list).toBeVisible()
  await expect(editor).toBeVisible()
  const [listBox, editorBox] = await Promise.all([list.boundingBox(), editor.boundingBox()])
  expect(listBox && editorBox).toBeTruthy()
  expect(listBox!.x + listBox!.width).toBeLessThanOrEqual(editorBox!.x)
  expect(Math.abs(listBox!.y - editorBox!.y)).toBeLessThan(1)
  await assertInventoryPage(page, 1440)
})
