import { expect, test } from '@playwright/test'
import { assertResponsiveContracts } from './routeFixtures'
import { installOperational, issue, settlePage, snapshot } from './fixtures'

const repair = { ...issue, claim: { park_id: 7 }, workflow: {
  owner: { login: 'mechanic-e2e', display: 'Механик смены' }, review_state: null,
  display_status: 'in_progress' as const, sync_state: 'synced' as const, has_current_cycle_comment: true,
} }

for (const width of [320, 390, 412, 899, 1440]) for (const theme of ['light', 'dark'] as const) {
  test(`Classic task and robot composition ${width}px ${theme}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 })
    await page.addInitScript(value => localStorage.setItem('robopark-theme', value), theme)
    await installOperational(page, { role: 'mechanic', issue: repair })
    await page.goto('/work/ROBOPARK-42?park=7')
    await settlePage(page)

    await expect(page.locator('.classic-task-layout')).toBeVisible()
    await expect(page.locator('.classic-task-layout [data-task-header]')).toBeVisible()
    await expect(page.locator('.classic-task-layout [data-task-body]')).toBeVisible()
    await expect(page.getByRole('tablist', { name: 'Разделы задачи' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Передать на проверку' })).toBeVisible()
    await assertResponsiveContracts(page, width)
    await page.screenshot({ path: info.outputPath(`work-classic-${theme}-${width}.png`), fullPage: true, animations: 'disabled' })

    await page.goto(`/robots/${snapshot.vin}?park=7&tab=scheme`)
    await expect(page.getByTestId('robot-check-layout')).toHaveClass(/classic-robot-layout/)
    await expect(page.getByText('АКБ 1', { exact: true })).toBeVisible()
    await expect(page.getByText('АКБ 2', { exact: true })).toBeVisible()
    await expect(page.getByRole('list', { name: 'Параметры связи' })).toContainText('SIM 2:')
    await assertResponsiveContracts(page, width)
    await page.screenshot({ path: info.outputPath(`robot-classic-${theme}-${width}.png`), fullPage: true, animations: 'disabled' })
  })
}
