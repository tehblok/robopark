import { expect, type Page } from '@playwright/test'

export async function selectInterface(page: Page, label: 'Классический' | 'Новый А') {
  await page.locator('.rp-shell__topbar, .rp-shell__bottom-nav').getByRole('button', { name: 'Ещё', exact: true }).click()
  await page.getByRole('radio', { name: label, exact: true }).check()
  await expect(page.locator('html')).toHaveAttribute('data-interface', label === 'Классический' ? 'classic' : 'task-first')
  await page.keyboard.press('Escape')
}
