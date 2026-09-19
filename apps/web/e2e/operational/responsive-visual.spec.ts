import { expect, test, type Page } from '@playwright/test'
import type { EmergencyReading, EmergencySnapshot, TrackerIssueDetail } from '../../src/api'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'
import { installOperational, issue, settlePage, snapshot } from './fixtures'
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
const claimedIssue: TrackerIssueDetail = {
  ...issue,
  assignee: { display: 'mechanic-e2e', login: 'mechanic-e2e' },
  claim: { park_id: 7 },
  workflow: {
    owner: { display: 'mechanic-e2e', login: 'mechanic-e2e' },
    review_state: null,
    display_status: 'in_progress',
    sync_state: 'saved',
    has_current_cycle_comment: true,
  },
}
const states = [
  { name: 'work', path: '/work/ROBOPARK-42?park=7&status=open&sort=newest&page=2', ready: '.rp-work-detail-pane' },
  { name: 'robots', path: '/robots?park=7', ready: '.rp-robots-search-panel' },
  { name: 'robot-check', path: `/robots/${snapshot.vin}/check?park=7&tab=scheme`, ready: '.rp-check-photo-frame img' },
  { name: 'inventory', path: '/inventory?park=7', ready: '[data-inventory-workflow="parts"]' },
] as const

test('legacy settings page keeps a readable phone gutter', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 })
  await installOperational(page, { role: 'royal' })
  await page.goto('/admin/emergency/config?tab=errors')
  const heading = page.getByRole('heading', { name: 'Настройки проверки робота' })
  await expect(heading).toBeVisible()
  const bounds = await heading.boundingBox()
  expect(bounds).toBeTruthy()
  expect(bounds!.x).toBeGreaterThanOrEqual(10)
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(380)
})

test('inventory selection box remains compact on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 })
  await installOperational(page, { role: 'mechanic' })
  await page.goto('/inventory?park=7')
  const checkbox = page.getByRole('checkbox', { name: 'Выбрать для печати Комплект крепежа' })
  await expect(checkbox).toBeVisible()
  const box = await checkbox.boundingBox()
  expect(box).toBeTruthy()
  expect(box!.width).toBeLessThanOrEqual(24)
  expect(box!.height).toBeLessThanOrEqual(24)
  const label = page.locator('.inventory-label-choice', { has: checkbox })
  const tapTarget = await label.boundingBox()
  expect(tapTarget!.height).toBeGreaterThanOrEqual(44)
  await label.click()
  await expect(checkbox).toBeChecked()
})

test('robot check keeps robot overview left and diagnostic block right', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await installOperational(page, { role: 'mechanic', snapshot: measuredSnapshot })
  await page.goto(`/robots/${snapshot.vin}/check?park=7&tab=scheme`)
  const summary = await page.locator('.rp-check-summary').boundingBox()
  const photo = await page.locator('.rp-check-photo-frame').boundingBox()
  const block = await page.locator('.rp-check-diagnostic-block').boundingBox()
  expect(summary && photo && block).toBeTruthy()
  expect(summary!.x + summary!.width).toBeLessThanOrEqual(block!.x)
  expect(photo!.x + photo!.width).toBeLessThanOrEqual(block!.x)
  expect(block!.y).toBeLessThan(photo!.y + photo!.height)
  expect(photo!.y).toBeLessThan(900)
})

test('diagnostic reading is shown on its own block diagram', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await installOperational(page, { role: 'mechanic', snapshot: measuredSnapshot })
  await page.goto(`/robots/${snapshot.vin}/check?park=7&tab=scheme`)
  const block = page.getByRole('region', { name: 'Диагностический блок «Колёса»' })
  const marker = block.getByRole('button', { name: 'Показание: Левый парктроник, 18 см' })
  await expect(marker).toBeVisible()
  await expect(page.locator('.rp-check-photo-frame .rp-check-reading-marker')).toHaveCount(0)
  const robot = await block.locator('.rp-check-block-photo-frame').boundingBox()
  const point = await marker.boundingBox()
  expect(robot && point).toBeTruthy()
  expect(point!.x + point!.width / 2).toBeGreaterThan(robot!.x)
  expect(point!.x + point!.width / 2).toBeLessThan(robot!.x + robot!.width)
})

test('robot check shows compact battery metrics before the photo on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 })
  await installOperational(page, { role: 'mechanic', snapshot: measuredSnapshot })
  await page.goto(`/robots/${snapshot.vin}/check?park=7&tab=scheme`)
  const battery1 = await page.locator('.rp-check-summary-values > div').nth(0).boundingBox()
  const battery2 = await page.locator('.rp-check-summary-values > div').nth(1).boundingBox()
  const photo = await page.locator('.rp-check-photo-frame').boundingBox()
  const block = await page.locator('.rp-check-diagnostic-block').boundingBox()
  expect(battery1 && battery2 && photo && block).toBeTruthy()
  expect(Math.abs(battery1!.y - battery2!.y)).toBeLessThan(2)
  expect(battery1!.y).toBeLessThan(photo!.y)
  expect(photo!.y).toBeLessThan(block!.y)
})

async function assertPhotoGeometry(page: Page) {
  const photo = page.locator('.rp-check-photo-frame img')
  await expect.poll(() => photo.evaluate((element: HTMLImageElement) => element.naturalWidth)).toBeGreaterThan(0)
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
  const robot = await page.locator('.rp-check-block-photo-frame').boundingBox()
  expect(robot).toBeTruthy()
  await expect(page.locator('.rp-check-diagnostic-block dl')).toContainText('Ток колеса')
  for (const marker of await page.locator('.rp-check-block-photo-frame button:visible').all()) {
    const box = await marker.boundingBox()
    expect(box).toBeTruthy()
    expect(box!.width, await marker.getAttribute('aria-label')).toBeGreaterThanOrEqual(44)
    expect(box!.height, await marker.getAttribute('aria-label')).toBeGreaterThanOrEqual(44)
    expect(box!.x + box!.width / 2).toBeGreaterThan(robot!.x)
    expect(box!.x + box!.width / 2).toBeLessThan(robot!.x + robot!.width)
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
    await expect(page.locator('.rp-check-diagnostic-block dl')).toContainText('4,2 А')
    await assertRobotReadingGeometry(page)
    await assertResponsiveContracts(page, width)
    await assertNoSeriousA11yViolations(page)
  })
}

test('200% text zoom at an equivalent 720 CSS-pixel viewport keeps primary actions operable', async ({ page }) => {
  await page.setViewportSize({ width: 720, height: 900 })
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
  await expect(save).toBeEnabled()
  await save.focus()
  await expect(save).toBeFocused()
  expect(await page.evaluate(() => window.innerWidth)).toBe(720)
  await assertResponsiveContracts(page, 720)

  await installOperational(page, { role: 'mechanic', snapshot: measuredSnapshot })
  await page.goto(`/robots/${snapshot.vin}/check?park=7&tab=scheme`)
  await page.evaluate(() => { document.documentElement.style.fontSize = '200%' })
  const robotPhoto = page.getByRole('img', { name: /Робот: вид/ })
  await robotPhoto.scrollIntoViewIfNeeded()
  await expect(robotPhoto).toBeInViewport()
  await expect(page.getByRole('button', { name: 'Показание: Левый парктроник, 18 см' })).toBeVisible()
  await expect(page.getByRole('group', { name: 'Ракурс модели' })).toHaveCount(0)
  await assertResponsiveContracts(page, 720)
})

for (const boundary of [
  { width: 899, mode: 'sequential', filterColumns: 2 },
  { width: 900, mode: 'compact split', filterColumns: 2 },
  { width: 1199, mode: 'compact split', filterColumns: 2 },
  { width: 1200, mode: 'wide split', filterColumns: 2 },
] as const) {
  test(`responsive boundary ${boundary.width}: ${boundary.mode}`, async ({ page }) => {
    await page.setViewportSize({ width: boundary.width, height: 900 })
    await installOperational(page, { issue: claimedIssue })
    await page.goto('/work/ROBOPARK-42?park=7&status=open&sort=newest&page=2')
    await expect(page.locator('.rp-work-detail-pane')).toBeVisible()
    await expect(page.getByRole('textbox', { name: 'Комментарии', exact: true })).toBeVisible()
    await settlePage(page)
    await assertWorkMode(page, boundary.width)
    await expect(page.getByRole('heading', { name: 'Задача ROBOPARK-42', exact: true })).toBeVisible()
    // The contract is usable filters without clipping, not a fixed CSS column count.
    if (boundary.mode === 'sequential') {
      await expect(page.getByRole('button', { name: 'Назад к списку', exact: true })).toBeVisible()
      await page.getByRole('button', { name: 'Назад к списку', exact: true }).click()
      await expect(page.getByRole('combobox', { name: 'Статус задач' })).toBeVisible()
    } else {
      await expect(page.getByRole('combobox', { name: 'Статус задач' })).toBeVisible()
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
    await installOperational(page, state.name === 'work' ? { issue: claimedIssue } : {})
    await page.goto(state.path)
    await expect(page.locator(state.ready)).toBeVisible()
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
    await settlePage(page)
    if (state.name === 'work') {
      await assertWorkMode(page, width)
      await expect(page.getByRole('textbox', { name: 'Комментарии', exact: true })).toBeVisible()
    }
    if (state.name === 'robot-check') {
      await page.locator('.rp-check-photo-frame img').scrollIntoViewIfNeeded()
      await expect.poll(() => page.locator('.rp-check-photo-frame img').evaluate((element: HTMLImageElement) => element.naturalWidth)).toBeGreaterThan(0)
      if (width <= 390) await assertPhotoGeometry(page)
      await page.evaluate(() => window.scrollTo(0, 0))
    }
    await assertResponsiveContracts(page, width)
    await assertNoSeriousA11yViolations(page)
    await expect(page).toHaveScreenshot(`${state.name}-${theme}-${width}.png`, { animations: 'disabled', caret: 'hide', fullPage: true })
  })
}

test('1440px 200% root text reflow preserves Work triage and detail', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await installOperational(page, { issue: claimedIssue })
  await page.goto('/work?park=7')
  await page.evaluate(() => { document.documentElement.style.fontSize = '200%' })
  for (const target of [
    page.getByRole('heading', { name: 'Очередь задач' }),
    page.getByRole('button', { name: /^Открыть задачу ROBOPARK-42:/ }),
  ]) {
    await target.scrollIntoViewIfNeeded()
    await expect(target).toBeInViewport()
  }
  await assertResponsiveContracts(page, 1440)
  await page.getByRole('button', { name: /^Открыть задачу ROBOPARK-42:/ }).click()
  await expect(page.getByRole('heading', { name: 'Задача ROBOPARK-42', exact: true })).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Комментарии', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Передать на проверку', exact: true })).toBeVisible()
  await assertResponsiveContracts(page, 1440)
})

test('legacy Dashboard URL redirects to Work with the selected park', async ({ page }) => {
  await installOperational(page)
  await page.goto('/dashboard?park=7')
  await expect(page).toHaveURL('/work?park=7')
  await expect(page.getByRole('heading', { name: 'Очередь задач' })).toBeVisible()
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
