import { expect, test } from '@playwright/test'
import { selectInterface } from '../support/interfaceMode'
import { assertResponsiveContracts } from './routeFixtures'
import { installOperational, issue, settlePage, snapshot } from './fixtures'

const repair = { ...issue, claim: { park_id: 7 }, workflow: {
  owner: { login: 'mechanic-e2e', display: 'Механик смены' }, review_state: null,
  display_status: 'in_progress' as const, sync_state: 'synced' as const, has_current_cycle_comment: true,
} }

for (const width of [320, 390, 412, 899, 1440]) for (const theme of ['light', 'dark'] as const) {
  test(`promised A task and robot composition ${width}px ${theme}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 })
    await page.addInitScript(value => localStorage.setItem('robopark-theme', value), theme)
    await installOperational(page, { role: 'mechanic', issue: repair })
    await page.goto('/work/ROBOPARK-42?park=7')
    await selectInterface(page, 'Новый А')
    await settlePage(page)

    const workflow = page.getByTestId('task-workflow-zone')
    const context = page.getByTestId('task-context-zone')
    const action = page.getByTestId('task-action-zone')
    await expect(workflow).toBeVisible()
    await expect(context).toBeVisible()
    await expect(action).toBeVisible()
    await expect(action.getByRole('button', { name: 'Передать на проверку' })).toBeVisible()
    if (width >= 900) {
      const center = await workflow.boundingBox()
      const rail = await context.boundingBox()
      expect(center && rail).toBeTruthy()
      expect(center!.x + center!.width).toBeLessThanOrEqual(rail!.x)
      await expect(context.getByRole('heading', { name: 'Робот сейчас' })).toBeVisible()
    } else {
      const disclosure = context.locator('details')
      await expect(disclosure).not.toHaveAttribute('open')
      await disclosure.locator('summary').click()
      await expect(context.getByRole('heading', { name: 'Робот сейчас' })).toBeVisible()
    }
    await assertResponsiveContracts(page, width)
    await page.screenshot({ path: info.outputPath(`work-a-${theme}-${width}.png`), fullPage: true, animations: 'disabled' })

    await page.goto(`/robots/${snapshot.vin}?park=7&tab=scheme`)
    await expect(page.getByTestId('robot-check-layout')).toHaveClass(/a-robot-layout/)
    await expect(page.getByText('АКБ 1', { exact: true })).toBeVisible()
    await expect(page.getByText('АКБ 2', { exact: true })).toBeVisible()
    await expect(page.getByRole('list', { name: 'Параметры связи' })).toContainText('SIM 2:')
    await assertResponsiveContracts(page, width)
    await page.screenshot({ path: info.outputPath(`robot-a-${theme}-${width}.png`), fullPage: true, animations: 'disabled' })
  })
}

test('Classic never receives A task or robot structural classes', async ({ page }) => {
  await installOperational(page, { role: 'mechanic', issue: repair })
  await page.goto('/work/ROBOPARK-42?park=7')
  await expect(page.locator('.a-task-layout')).toHaveCount(0)
  await expect(page.locator('.classic-task-layout')).toBeVisible()
  await page.goto(`/robots/${snapshot.vin}?park=7&tab=scheme`)
  await expect(page.getByTestId('robot-check-layout')).toHaveClass(/classic-robot-layout/)
  await expect(page.getByTestId('robot-check-layout')).not.toHaveClass(/a-robot-layout/)
})
