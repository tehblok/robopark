import { expect, test } from '@playwright/test'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'
import { installOperational, parkNorth, roles, settlePage, snapshot, userForRole } from './fixtures'

const headings = {
  mechanic: 'Что требует внимания в смене', operator: 'Что мешает работе парка',
  driver: 'Можно ли безопасно продолжать работу', admin: 'Готовность людей и системы', royal: 'Главный риск доступных парков',
}
const actions = {
  mechanic: 'Открыть задачу ROBOPARK-42', operator: 'Разобрать ROBOPARK-42',
  driver: 'Найти или сканировать робота', admin: 'Настроить парк', royal: 'Открыть работу парка Южный парк',
}
for (const role of roles) {
  test(`${role}: primary operation is reachable within two clicks`, async ({ page }) => {
    const trackerRequests: string[] = []
    page.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/tracker/')) trackerRequests.push(request.url()) })
    const user = userForRole(role)
    if (role === 'admin') user.parks = [{ ...parkNorth, tracker_queue: null }]
    await installOperational(page, { user })
    await page.goto('/overview?park=7')
    await expect(page.getByRole('heading', { name: headings[role] })).toBeVisible()
    await expect(page.getByTestId('overview-action').getByRole('link')).toHaveText(actions[role])
    await settlePage(page)
    await assertNoSeriousA11yViolations(page)
    await page.getByTestId('overview-action').getByRole('link').click()
    if (role === 'driver') {
      await page.getByLabel('Номер или VIN робота').fill('447')
      await page.getByRole('button', { name: 'Найти робота', exact: true }).click()
      await expect(page).toHaveURL(new RegExp(`/robots/${snapshot.vin}(?:\\?|$)`))
      await expect(page.getByRole('heading', { name: 'Робот 447', exact: true })).toBeVisible()
      await settlePage(page)
      expect(trackerRequests).toEqual([])
    } else if (role === 'admin') {
      await expect(page).toHaveURL(/\/admin(?:\?|$)/)
      await expect(page.getByRole('heading', { name: 'Администрирование' })).toBeVisible()
    } else if (role === 'royal') {
      await expect(page).toHaveURL(/\/work\?park=8/)
      await expect(page.getByRole('heading', { name: 'Очередь задач', exact: true })).toBeVisible()
    } else {
      await expect(page).toHaveURL(/\/work\/ROBOPARK-42\?park=7/)
      await expect(page.getByRole('heading', { name: 'Задача ROBOPARK-42', exact: true })).toBeVisible()
    }
  })
}
