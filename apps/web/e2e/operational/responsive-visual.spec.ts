import { expect, test, type Page } from '@playwright/test'
import type { EmergencyReading, EmergencySnapshot } from '../../src/api'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'
import { installOperational, settlePage, snapshot } from './fixtures'
import { assertResponsiveContracts } from './routeFixtures'

const widths = [320, 390, 768, 1024, 1440] as const
const themes = ['light', 'dark'] as const
const configuredReading: EmergencyReading = {
  id: 1, section_id: 'wheels', path: 'parktronics.lt', label: 'Левый парктроник', display_kind: 'distance',
  unit: 'см', precision: 0, enabled_path: 'parktronics.ltEnabled', no_data_values: [2147483647],
  warning_below: 25, warning_above: null, critical_below: 10, critical_above: null,
  view: 'top', x: .24, y: .56, label_direction: 'left', is_enabled: true, sort_order: 0,
}
const measuredSnapshot: EmergencySnapshot = {
  ...snapshot,
  stale: false,
  stale_age_seconds: 0,
  battery1_connected: true,
  battery2_connected: true,
  readings: [
    { id: 1, section_id: 'wheels', label: 'Левый парктроник', display: '18 см', state: 'warning', view: 'top', x: .24, y: .56, label_direction: 'left' },
    { id: 2, section_id: 'wheels', label: 'Ток колеса', display: '4,2 А', state: 'normal', view: 'top', x: .72, y: .42, label_direction: 'right' },
  ],
  diagnostic_events: [],
}
const states = [
  { name: 'overview', path: '/overview?park=7', ready: '.rp-overview' },
  { name: 'work', path: '/work/ROBOPARK-42?park=7&status=open&sort=newest&page=2', ready: '.issue-actions' },
  { name: 'robots', path: '/robots?park=7', ready: '.rp-robots-search-panel' },
  { name: 'robot-check', path: `/robots/${snapshot.vin}/check?park=7&tab=scheme`, ready: '.rp-check-photo-frame img' },
  { name: 'inventory', path: '/inventory?park=7', ready: '[data-inventory-workflow="parts"]' },
] as const

async function assertPhotoGeometry(page: Page) {
  const photo = page.locator('.rp-check-photo-frame img')
  await expect.poll(() => photo.evaluate((element: HTMLImageElement) => element.naturalWidth)).toBe(2269)
  await expect(page.locator('.rp-check-wheel')).toHaveCount(0)
  await expect(page.locator('.rp-check-wheel-details')).toContainText('Неисправность: Переднее левое колесо')
}

async function assertWorkMode(page: Page, width: number) {
  for (const row of await page.locator('.rp-work-entities .rp-entity-row:visible').all()) {
    const age = await row.locator('.rp-work-issue-age').boundingBox()
    const status = await row.locator('.rp-entity-row__status').boundingBox()
    expect(age && status).toBeTruthy()
    expect(age!.x + age!.width <= status!.x || status!.x + status!.width <= age!.x
      || age!.y + age!.height <= status!.y || status!.y + status!.height <= age!.y).toBe(true)
  }
  await expect(page.locator('.rp-work-detail-pane')).toBeVisible()
  if (width >= 900) {
    await expect(page.locator('.rp-work-list-pane')).toBeInViewport()
    await expect(page.locator('.rp-work-detail-pane')).toBeInViewport()
  } else await expect(page.locator('.rp-work-list-pane')).toBeHidden()
}

async function assertRobotReadingGeometry(page: Page) {
  const robot = await page.locator('.rp-check-photo-frame').boundingBox()
  expect(robot).toBeTruthy()
  const labels = page.locator('.rp-check-marker-label:visible')
  for (const label of await labels.all()) {
    const box = await label.boundingBox()
    expect(box).toBeTruthy()
    const separated = box!.x + box!.width <= robot!.x
      || robot!.x + robot!.width <= box!.x
      || box!.y + box!.height <= robot!.y
      || robot!.y + robot!.height <= box!.y
    expect(separated, await label.textContent()).toBe(true)
  }
  await expect(labels.or(page.locator('.rp-check-collapsed-labels li'))).not.toHaveCount(0)
  for (const marker of await page.locator('.rp-check-photo-frame button:visible').all()) {
    const box = await marker.boundingBox()
    expect(box).toBeTruthy()
    expect(box!.width, await marker.getAttribute('aria-label')).toBeGreaterThanOrEqual(44)
    expect(box!.height, await marker.getAttribute('aria-label')).toBeGreaterThanOrEqual(44)
  }
}

for (const width of widths) for (const theme of themes) {
  test(`robot readings and admin catalog reflow at ${width}px in ${theme}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await page.addInitScript(themeName => localStorage.setItem('robopark-theme', themeName), theme)
    await installOperational(page, {
      role: 'admin',
      routes: [
        { method: 'GET', path: '/api/admin/emergency/sections', handler: () => ({ json: [{ id: 'wheels', title: 'Колёса', is_enabled: true, roles: ['mechanic', 'admin'], fields: [], sort_order: 0 }] }) },
        { method: 'GET', path: '/api/admin/emergency-readings', handler: () => ({ json: [configuredReading], headers: { ETag: '"readings-1"' } }) },
      ],
    })
    await page.goto('/admin/emergency/config?park=7&tab=readings')
    await expect(page.getByRole('heading', { name: 'Каталог показаний' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Новое показание' })).toBeVisible()
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
    await assertResponsiveContracts(page, width)

    await installOperational(page, { role: 'mechanic', snapshot: measuredSnapshot })
    await page.goto(`/robots/${snapshot.vin}/check?park=7&tab=scheme`)
    await expect(page.getByRole('button', { name: 'Показание: Ток колеса, 4,2 А' })).toBeVisible()
    await assertRobotReadingGeometry(page)
    const marker = page.getByRole('button', { name: 'Показание: Ток колеса, 4,2 А' })
    await marker.focus()
    await page.keyboard.press('Enter')
    await expect(marker).toHaveAttribute('aria-pressed', 'true')
    await expect(page.getByText('Ток колеса: 4,2 А', { exact: true })).toBeVisible()
    await assertResponsiveContracts(page, width)
    await assertNoSeriousA11yViolations(page)
  })
}

test('200% text zoom keeps robot and catalog primary actions reachable', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await installOperational(page, {
    role: 'admin',
    routes: [
      { method: 'GET', path: '/api/admin/emergency/sections', handler: () => ({ json: [{ id: 'wheels', title: 'Колёса', is_enabled: true, roles: ['mechanic', 'admin'], fields: [], sort_order: 0 }] }) },
      { method: 'GET', path: '/api/admin/emergency-readings', handler: () => ({ json: [configuredReading] }) },
    ],
  })
  await page.goto('/admin/emergency/config?park=7&tab=readings')
  await page.getByRole('button', { name: 'Открыть показание Левый парктроник' }).click()
  await page.evaluate(() => { document.documentElement.style.fontSize = '200%' })
  const save = page.getByRole('button', { name: 'Сохранить показание' })
  await save.scrollIntoViewIfNeeded()
  await expect(save).toBeInViewport()
  await assertResponsiveContracts(page, 1440)

  await installOperational(page, { role: 'mechanic', snapshot: measuredSnapshot })
  await page.goto(`/robots/${snapshot.vin}/check?park=7&tab=scheme`)
  await page.evaluate(() => { document.documentElement.style.fontSize = '200%' })
  const primaryView = page.getByRole('button', { name: 'Сверху', exact: true })
  await primaryView.scrollIntoViewIfNeeded()
  await expect(primaryView).toBeInViewport()
  await expect(page.getByRole('button', { name: 'Показание: Левый парктроник, 18 см' })).toBeVisible()
  await assertResponsiveContracts(page, 1440)
})

for (const boundary of [
  { width: 899, mode: 'sequential', filterColumns: 2 },
  { width: 900, mode: 'compact split', filterColumns: 2 },
  { width: 1199, mode: 'compact split', filterColumns: 2 },
  { width: 1200, mode: 'wide split', filterColumns: 2 },
] as const) {
  test(`responsive boundary ${boundary.width}: ${boundary.mode}`, async ({ page }) => {
    await page.setViewportSize({ width: boundary.width, height: 900 })
    await installOperational(page)
    await page.goto('/work/ROBOPARK-42?park=7&status=open&sort=newest&page=2')
    await expect(page.locator('.issue-actions')).toBeVisible()
    await settlePage(page)
    await assertWorkMode(page, boundary.width)
    await expect(page.getByRole('heading', { name: 'Задача ROBOPARK-42', exact: true })).toBeVisible()
    const columns = await page.locator('.rp-work-filters').evaluate(element => getComputedStyle(element).gridTemplateColumns.split(' ').length)
    expect(columns).toBe(boundary.filterColumns)
    if (boundary.mode === 'sequential') {
      await expect(page.getByRole('button', { name: 'Назад к списку', exact: true })).toBeVisible()
    } else {
      const list = await page.locator('.rp-work-list-pane').boundingBox()
      const detail = await page.locator('.rp-work-detail-pane').boundingBox()
      expect(list!.x + list!.width).toBeLessThanOrEqual(detail!.x)
      expect(Math.abs(list!.y - detail!.y)).toBeLessThan(1)
      await expect(page.getByRole('button', { name: 'Назад к списку', exact: true })).toBeHidden()
    }
    await assertResponsiveContracts(page, boundary.width)
  })
}

for (const width of widths) for (const theme of themes) for (const state of states) {
  test(`${state.name}-${theme}-${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await page.addInitScript(theme => localStorage.setItem('robopark-theme', theme), theme)
    await installOperational(page)
    await page.goto(state.path)
    await expect(page.locator(state.ready)).toBeVisible()
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
    await settlePage(page)
    if (state.name === 'overview' && width >= 900) {
      const statuses = page.getByRole('heading', { name: 'Статусы задач' })
      const queue = page.getByRole('heading', { name: 'Очередь внимания' })
      const nextAction = page.getByRole('link', { name: /^Открыть задачу / }).first()
      const flow = page.locator('.rp-overview-flow')
      const flowHeading = page.getByRole('heading', { name: 'Поток задач: пришло / ушло' })
      await expect(statuses).toBeVisible()
      await expect(queue).toBeVisible()
      await expect(nextAction).toBeVisible()
      await expect(flow).toBeVisible()
      const order = await Promise.all([statuses, queue, flowHeading].map(locator => locator.evaluate(node =>
        [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].indexOf(node as HTMLHeadingElement),
      )))
      expect(order[0]).toBeLessThan(order[1])
      expect(order[1]).toBeLessThan(order[2])
    }
    if (state.name === 'work') {
      await assertWorkMode(page, width)
    }
    if (state.name === 'robot-check') {
      await page.locator('.rp-check-photo-frame img').scrollIntoViewIfNeeded()
      await expect.poll(() => page.locator('.rp-check-photo-frame img').evaluate((element: HTMLImageElement) => element.naturalWidth)).toBe(2269)
      if (width <= 390) await assertPhotoGeometry(page)
      await page.evaluate(() => window.scrollTo(0, 0))
    }
    await assertResponsiveContracts(page, width)
    await assertNoSeriousA11yViolations(page)
    await expect(page).toHaveScreenshot(`${state.name}-${theme}-${width}.png`, { animations: 'disabled', caret: 'hide', fullPage: true })
  })
}

test('1440px 200% root text reflow preserves Overview triage and detail', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await installOperational(page)
  await page.goto('/overview?park=7')
  await page.evaluate(() => { document.documentElement.style.fontSize = '200%' })
  for (const target of [
    page.locator('.rp-overview-flow'),
    page.getByRole('heading', { name: 'Статусы задач' }),
    page.getByRole('heading', { name: 'Очередь внимания' }),
  ]) {
    await target.scrollIntoViewIfNeeded()
    await expect(target).toBeInViewport()
  }
  await assertResponsiveContracts(page, 1440)
  await page.getByRole('link', { name: 'Открыть задачу ROBOPARK-42' }).click()
  await expect(page.getByRole('heading', { name: 'Задача ROBOPARK-42', exact: true })).toBeVisible()
  await page.locator('.issue-actions').scrollIntoViewIfNeeded()
  await expect(page.getByRole('button', { name: 'Закрыть тикет', exact: true })).toBeVisible()
  await assertResponsiveContracts(page, 1440)
})

test('system dark theme survives reload without losing URL and search state', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.addInitScript(() => localStorage.setItem('robopark-theme', 'system'))
  await installOperational(page)
  await page.goto('/robots?park=7&q=447')
  await expect(page.getByLabel('Номер или VIN робота')).toHaveValue('447')
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await expect(page).toHaveURL('/robots?park=7&q=447')
  await expect(page.getByLabel('Номер или VIN робота')).toHaveValue('447')
})
