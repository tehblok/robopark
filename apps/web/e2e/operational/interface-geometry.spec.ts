import { expect, test } from '@playwright/test'
import { assertResponsiveContracts, assertRouteSemanticContracts, geometryRouteIdsFor, openRouteFixture } from './routeFixtures'
import { installOperational, issue, userForRole, settlePage } from './fixtures'

const geometryRoles = ['driver', 'mechanic', 'operator', 'admin', 'royal'] as const
const geometryThemes = ['light', 'dark'] as const
const geometryViewports = [
  { name: 'phone-360', width: 360, height: 800 },
  { name: 'phone-390', width: 390, height: 844 },
  { name: 'desktop', width: 1440, height: 1000 },
] as const

test('operational text stays readable on a phone and desktop', async ({ page }) => {
  for (const width of [390, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    await openRouteFixture(page, 'overview', userForRole('royal'))
    const bodySize = await page.locator('body').evaluate(element => parseFloat(getComputedStyle(element).fontSize))
    expect(bodySize).toBeGreaterThanOrEqual(16)
  }
})

for (const role of geometryRoles) {
  test(`${role} overview task table becomes inset cards without page overflow on a phone`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await openRouteFixture(page, 'overview', userForRole(role))
    await settlePage(page)
    const table = page.getByRole('table', { name: 'Задачи смены' })
    await expect(table).toBeVisible()
    await expect(table.getByRole('columnheader', { name: 'Исполнитель' })).toHaveCount(1)
    const row = table.locator('tbody tr').first()
    await expect(row).toBeVisible()
    expect(await row.evaluate(element => getComputedStyle(element).display)).toBe('grid')
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
    const bounds = await row.boundingBox()
    expect(bounds).not.toBeNull()
    expect(bounds!.x).toBeGreaterThanOrEqual(8)
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(382)
    await page.setViewportSize({ width: 1440, height: 900 })
    expect(await row.evaluate(element => getComputedStyle(element).display)).toBe('table-row')
    await expect(table.getByRole('columnheader', { name: 'Исполнитель' })).toBeVisible()
  })
}

test('small phone overview keeps task actions inside a 320px viewport', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 720 })
  await openRouteFixture(page, 'overview', userForRole('mechanic'))
  await settlePage(page)
  const row = page.getByRole('table', { name: 'Задачи смены' }).locator('tbody tr').first()
  await expect(row).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320)
  const action = await row.getByRole('link', { name: /Открыть задачу/ }).boundingBox()
  expect(action).not.toBeNull()
  expect(action!.x).toBeGreaterThanOrEqual(8)
  expect(action!.x + action!.width).toBeLessThanOrEqual(312)
})

test('small phone centers the campaign percentage inside its compact ring', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 720 })
  await openRouteFixture(page, 'overview', userForRole('mechanic'))
  const progress = page.locator('.campaign-overview-item .campaign-progress').first()
  await expect(progress).toBeVisible()
  const ring = await progress.boundingBox()
  const number = await progress.locator('strong').boundingBox()
  expect(ring && number).toBeTruthy()
  await expect(progress.locator('span')).toHaveCount(0)
  expect(number!.x, 'progress percentage left inset').toBeGreaterThanOrEqual(ring!.x + 4)
  expect(number!.x + number!.width, 'progress percentage right inset').toBeLessThanOrEqual(ring!.x + ring!.width - 4)
  expect(Math.abs(number!.x + number!.width / 2 - ring!.x - ring!.width / 2)).toBeLessThanOrEqual(1)
})

test('phone overview keeps status filters compact and readable', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openRouteFixture(page, 'overview', userForRole('royal'))
  await settlePage(page)
  const cards = page.locator('.rp-overview-statuses .rp-overview-status')
  const first = await cards.nth(0).boundingBox()
  const second = await cards.nth(1).boundingBox()
  expect(first).not.toBeNull()
  expect(second).not.toBeNull()
  expect(second!.x).toBeGreaterThan(first!.x + first!.width)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
})

for (const width of [905, 1440]) {
  test(`desktop topbar keeps synchronization centered and account controls separate at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await openRouteFixture(page, 'overview', userForRole('royal'))
    const sync = await page.locator('.rp-sync-center__trigger').boundingBox()
    const account = await page.locator('.rp-shell__topbar-actions').boundingBox()
    const topbar = await page.locator('.rp-shell__topbar').boundingBox()
    expect(sync).not.toBeNull()
    expect(account).not.toBeNull()
    expect(topbar).not.toBeNull()
    expect(sync!.x + sync!.width).toBeLessThanOrEqual(account!.x - 8)
    expect(Math.abs(sync!.x + sync!.width / 2 - topbar!.x - topbar!.width / 2)).toBeLessThanOrEqual(1)
  })
}

test('phone topbar separates park switch from synchronization at 360px', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 })
  await openRouteFixture(page, 'overview', userForRole('admin'))
  const park = await page.locator('.rp-shell__park-switch').boundingBox()
  const sync = await page.locator('.rp-sync-center__trigger').boundingBox()
  const brand = await page.locator('.rp-shell__park-brand').boundingBox()
  const grid = await page.locator('.rp-shell__topbar').evaluate(element => getComputedStyle(element).gridTemplateColumns)
  expect(park).not.toBeNull()
  expect(sync).not.toBeNull()
  expect(park!.x + park!.width, `park=${JSON.stringify(park)} sync=${JSON.stringify(sync)} brand=${JSON.stringify(brand)} grid=${grid}`).toBeLessThanOrEqual(sync!.x - 8)
})

test('robot check stacks summary and tabs when the desktop rail leaves a narrow workspace', async ({ page }) => {
  await page.setViewportSize({ width: 905, height: 900 })
  await openRouteFixture(page, 'robot-detail', userForRole('royal'))
  const summary = await page.locator('.rp-check-workspace .rp-check-layout__overview').boundingBox()
  const detail = await page.locator('.rp-check-workspace .rp-check-layout__details').boundingBox()
  expect(summary).not.toBeNull()
  expect(detail).not.toBeNull()
  expect(detail!.y).toBeGreaterThanOrEqual(summary!.y + summary!.height)
})

test('work queue stacks list and detail when the desktop rail leaves a narrow workspace', async ({ page }) => {
  await page.setViewportSize({ width: 905, height: 900 })
  await openRouteFixture(page, 'work', userForRole('royal'))
  const list = await page.locator('.rp-workbench .rp-master-detail__list').boundingBox()
  const detail = await page.locator('.rp-workbench .rp-master-detail__detail').boundingBox()
  expect(list).not.toBeNull()
  expect(detail).not.toBeNull()
  expect(detail!.y).toBeGreaterThanOrEqual(list!.y + list!.height)
})

test('reports stack list and detail when the desktop rail leaves a narrow workspace', async ({ page }) => {
  await page.setViewportSize({ width: 905, height: 900 })
  await openRouteFixture(page, 'reports', userForRole('royal'))
  const list = await page.locator('.rp-master-detail__list').boundingBox()
  const detail = await page.locator('.rp-master-detail__detail').boundingBox()
  expect(list).not.toBeNull()
  expect(detail).not.toBeNull()
  expect(detail!.y).toBeGreaterThanOrEqual(list!.y + list!.height)
})

test('robot-check settings stack catalog and editor at narrow desktop width', async ({ page }) => {
  await page.setViewportSize({ width: 905, height: 900 })
  await openRouteFixture(page, 'admin-robot-check', userForRole('royal'))
  await page.getByRole('tab', { name: 'Ошибки', exact: true }).click()
  await settlePage(page)
  const catalog = await page.locator('.rp-diagnostic-panel .rp-master-detail__list').boundingBox()
  const editor = await page.locator('.rp-diagnostic-panel .rp-master-detail__detail').boundingBox()
  expect(catalog).not.toBeNull()
  expect(editor).not.toBeNull()
  expect(editor!.y).toBeGreaterThanOrEqual(catalog!.y + catalog!.height)
})

test('diagnostic severity icon and label share one centered badge row', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openRouteFixture(page, 'admin-robot-check', userForRole('royal'))
  await settlePage(page)
  const badge = page.locator('.rp-diagnostic-select .rp-status-badge').first()
  await expect(badge).toBeVisible()
  const outer = await badge.boundingBox()
  const icon = await badge.locator('svg').boundingBox()
  expect(outer && icon).toBeTruthy()
  expect(Math.abs(icon!.y + icon!.height / 2 - outer!.y - outer!.height / 2)).toBeLessThan(4)
})

test('mobile task detail keeps text inset from its panel edge', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openRouteFixture(page, 'work-issue', userForRole('royal'))
  await settlePage(page)
  const panel = page.locator('.rp-work-detail-pane > .rp-panel')
  const geometry = await panel.evaluate(element => ({
    padding: parseFloat(getComputedStyle(element).paddingInlineStart),
    compactInset: parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--rp-space-2')),
  }))
  expect(geometry.padding).toBe(geometry.compactInset)
})

test('phone task keeps related robot tasks on one compact row', async ({ page }) => {
  for (const width of [320, 390]) {
    await page.setViewportSize({ width, height: 844 })
    await openRouteFixture(page, 'work-issue', userForRole('mechanic'))
    await settlePage(page)
    const open = await page.getByRole('tab', { name: 'Открытые задачи' }).boundingBox()
    const closed = await page.getByRole('tab', { name: 'Закрытые задачи' }).boundingBox()
    expect(open).not.toBeNull()
    expect(closed).not.toBeNull()
    expect(Math.abs(closed!.y - open!.y)).toBeLessThanOrEqual(1)
    expect(closed!.x).toBeGreaterThanOrEqual(open!.x + open!.width)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  }
})

test('legacy task handoff has one disclosure and an unobscured mobile save action', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 })
  await installOperational(page, { user: userForRole('mechanic'), issue: { ...issue, claim: { park_id: 7 }, workflow: undefined } })
  await page.goto('/work/ROBOPARK-42?park=7')
  const handoff = page.getByRole('button', { name: 'Передача смены' })
  await handoff.click()
  const panel = page.locator('.issue-collaboration')
  await expect(panel).toBeVisible()
  await expect(panel.locator('details')).toHaveCount(0)
  const save = panel.getByRole('button', { name: 'Сохранить передачу смены' })
  await save.scrollIntoViewIfNeeded()
  const saveBox = await save.boundingBox()
  const navBox = await page.locator('.rp-shell__bottom-nav').boundingBox()
  expect(saveBox && navBox).toBeTruthy()
  expect(saveBox!.y + saveBox!.height).toBeLessThan(navBox!.y)
})

test('phone system resource cards use a readable single column', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openRouteFixture(page, 'system', userForRole('royal'))
  await settlePage(page)
  const cards = page.locator('.rp-system-grid').first().locator(':scope > *')
  const first = await cards.nth(0).boundingBox()
  const second = await cards.nth(1).boundingBox()
  expect(first).not.toBeNull()
  expect(second).not.toBeNull()
  expect(second!.y).toBeGreaterThanOrEqual(first!.y + first!.height)
})

test('system metric keeps its value and hint together beside a taller chart', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openRouteFixture(page, 'system', userForRole('royal'))
  await page.route('**/api/admin/system/history**', route => route.fulfill({ json: {
    active_users: [{ date: '2026-09-01', users: 3 }, { date: '2026-09-02', users: 4 }],
    metrics: [],
  } }))
  await page.reload()
  await settlePage(page)
  const rows = await page.locator('.rp-system-overview .rp-metric-card').evaluate(card => {
    const label = card.querySelector('.rp-metric-card__label')!.getBoundingClientRect()
    const value = card.querySelector('.rp-metric-card__value')!.getBoundingClientRect()
    const hint = card.querySelector('.rp-metric-card__delta')!.getBoundingClientRect()
    return {
      cardHeight: card.getBoundingClientRect().height,
      labelHeight: label.height, valueHeight: value.height, hintHeight: hint.height,
      labelToValue: value.top - label.bottom, valueToHint: hint.top - value.bottom,
    }
  })
  expect(rows.cardHeight).toBeLessThanOrEqual(140)
  expect(rows.labelHeight).toBeLessThanOrEqual(40)
  expect(rows.valueHeight).toBeLessThanOrEqual(40)
  expect(rows.hintHeight).toBeLessThanOrEqual(40)
  expect(rows.labelToValue).toBeLessThanOrEqual(24)
  expect(rows.valueToHint).toBeLessThanOrEqual(24)
})

test('phone robot summary gives battery values enough width', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openRouteFixture(page, 'robot-detail', userForRole('royal'))
  await settlePage(page)
  const values = page.locator('.rp-check-summary-values > div')
  const batteryOne = await values.nth(0).boundingBox()
  const batteryTwo = await values.nth(1).boundingBox()
  const speed = await values.nth(2).boundingBox()
  expect(batteryOne).not.toBeNull()
  expect(batteryTwo).not.toBeNull()
  expect(speed).not.toBeNull()
  expect(batteryTwo!.y).toBe(batteryOne!.y)
  expect(speed!.y).toBeGreaterThanOrEqual(batteryOne!.y + batteryOne!.height)
})

test('phone overview status cards fill the available row', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openRouteFixture(page, 'overview', userForRole('royal'))
  await settlePage(page)
  const link = page.locator('.rp-overview-status').first()
  const outer = await link.boundingBox()
  const card = await link.locator('.rp-metric-card').boundingBox()
  expect(outer).not.toBeNull()
  expect(card).not.toBeNull()
  expect(card!.width).toBeGreaterThanOrEqual(outer!.width - 1)
})

test('schedule route fixture reaches the successful schedule workspace', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openRouteFixture(page, 'schedule', userForRole('mechanic'))
  await expect(page.getByTestId('schedule-day-cards')).toBeVisible()
  await expect(page.getByText('Не удалось загрузить график', { exact: true })).toHaveCount(0)
  await assertResponsiveContracts(page, 390)
})

test('More menu uses the same dark surface as ordinary panels', async ({ page }) => {
  await page.setViewportSize({ width: 905, height: 900 })
  await page.addInitScript(() => localStorage.setItem('robopark-theme', 'dark'))
  await openRouteFixture(page, 'admin-robot-check', userForRole('royal'))
  await page.getByRole('button', { name: 'Ещё' }).click()
  const colors = await page.evaluate(() => ({
    menu: getComputedStyle(document.querySelector('.rp-dialog:has(.rp-shell-controls)')!).backgroundColor,
    panel: getComputedStyle(document.querySelector('.rp-shell__sidebar')!).backgroundColor,
  }))
  expect(colors.menu).toBe(colors.panel)
})

for (const theme of ['light', 'dark'] as const) {
  test(`work status and priority use semantic ${theme} colors`, async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem('robopark-theme', value), theme)
    await installOperational(page, {
      user: userForRole('royal'),
      issue: { ...issue, status: 'В работе', status_key: 'in_progress', priority: 'major' },
    })
    await page.goto('/work?park=7')
    await page.getByRole('button', { name: /Открыть задачу ROBOPARK-42/ }).click()
    await expect(page.locator('.issue-status.tone-progress').first()).toBeVisible()
    const colors = await page.evaluate(() => {
      const root = getComputedStyle(document.documentElement)
      const tokenColor = (name: string) => {
        const probe = document.createElement('span')
        probe.style.color = root.getPropertyValue(name).trim()
        document.body.append(probe)
        const color = getComputedStyle(probe).color
        probe.remove()
        return color
      }
      return {
        progress: getComputedStyle(document.querySelector('.issue-status.tone-progress')!).color,
        high: getComputedStyle(document.querySelector('.issue-badge.tone-high')!).color,
        info: tokenColor('--rp-info'),
        warning: tokenColor('--rp-warning'),
      }
    })
    expect(colors.progress).toBe(colors.info)
    expect(colors.high).toBe(colors.warning)
  })
}

test('analytics card contains its horizontal table viewport on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openRouteFixture(page, 'analytics', userForRole('royal'))
  await settlePage(page)
  const card = page.locator('.rp-analytics-card').filter({ hasText: 'Значения по интервалам' }).first()
  await card.getByText('Значения по интервалам', { exact: true }).click()
  await expect(card.getByRole('region', { name: /^Таблица:/ })).toBeVisible()
  const geometry = await card.evaluate(card => {
    const table = card.querySelector('.rp-analytics-table-scroll')!
    const outer = card.getBoundingClientRect()
    const inner = table.getBoundingClientRect()
    return { outer: [outer.left, outer.right], inner: [inner.left, inner.right], scrollWidth: table.scrollWidth, clientWidth: table.clientWidth }
  })
  expect(geometry.inner[0]).toBeGreaterThanOrEqual(geometry.outer[0] + 8)
  expect(geometry.inner[1]).toBeLessThanOrEqual(geometry.outer[1] - 8)
})

test('verified closures stay readable without page overflow on phone and desktop', async ({ page }) => {
  for (const width of [390, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    await openRouteFixture(page, 'analytics', userForRole('royal'))
    await settlePage(page)
    const panel = page.getByRole('region', { name: 'Подтверждённые закрытия' })
    await expect(panel).toBeVisible()
    await panel.getByText('Как считаем закрытия и простой', { exact: true }).click()
    await expect(panel.getByText('Медиана простоя')).toBeVisible()
    await expect(panel.getByText('90-й процентиль', { exact: true })).toBeVisible()
    const bounds = await panel.boundingBox()
    expect(bounds).not.toBeNull()
    expect(bounds!.x).toBeGreaterThanOrEqual(8)
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width - 8)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  }
})

test('geometry helper audits descendant text against its nearest bordered container', async ({ page }) => {
  await page.setViewportSize({ width: 1000, height: 800 })
  await page.setContent(`
    <style>* { box-sizing: border-box; font: 16px sans-serif } .outer { border: 1px solid; border-radius: 16px; padding: 0 } .own { border: 1px solid; border-radius: 16px; display: inline-flex; padding: 8px }</style>
    <div class="outer"><span>Too close</span></div>
    <div class="outer"><span class="own">Own border is padded</span></div>
  `)
  let failure = ''
  try { await assertResponsiveContracts(page, 1000) } catch (error) { failure = String(error) }
  expect(failure).toMatch(/text inset .*Too close/)
  expect(failure).not.toContain('Own border is padded')
})

test('geometry helper checks overlay controls against flow controls', async ({ page }) => {
  await page.setViewportSize({ width: 1000, height: 800 })
  await page.setContent(`
    <style>* { box-sizing: border-box; font: 16px sans-serif } button { min-width: 80px; min-height: 44px } .overlay { position: fixed; inset: 0 auto auto 0 }</style>
    <button>Flow action</button><button class="overlay">Overlay action</button>
  `)
  await expect(assertResponsiveContracts(page, 1000)).rejects.toThrow(/overlap .*Flow action.*Overlay action|overlap .*Overlay action.*Flow action/)
})

test('geometry helper includes summary and inline link hit areas', async ({ page }) => {
  await page.setViewportSize({ width: 1000, height: 800 })
  await page.setContent(`
    <style>* { box-sizing: border-box; font: 16px sans-serif } details { margin-bottom: 20px } summary, a { font-size: 16px }</style>
    <details><summary>Compact disclosure</summary><p>Content</p></details>
    <p><a href="#target">Compact inline link</a></p>
  `)
  await expect(assertResponsiveContracts(page, 1000)).rejects.toThrow(/target .*Compact disclosure/)
  await page.locator('summary').evaluate(element => { element.style.minHeight = '44px'; element.style.display = 'flex' })
  await expect(assertResponsiveContracts(page, 1000)).rejects.toThrow(/target .*Compact inline link/)
})

test('production disclosure keeps its native marker and 44px hit area', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openRouteFixture(page, 'analytics', userForRole('operator'))
  const summary = page.locator('.rp-analytics-card summary', { hasText: 'Значения по интервалам' }).first()
  await expect(summary).toBeVisible()
  const geometry = await summary.evaluate(element => {
    const style = getComputedStyle(element)
    const rect = element.getBoundingClientRect()
    return { display: style.display, listStyleType: style.listStyleType, height: rect.height }
  })
  expect(geometry.display).toBe('list-item')
  expect(geometry.listStyleType).not.toBe('none')
  expect(geometry.height).toBeGreaterThanOrEqual(44)
})

for (const role of geometryRoles) {
  const user = userForRole(role)
  for (const theme of geometryThemes) for (const viewport of geometryViewports) {
    test(`${role} routes satisfy ${theme} ${viewport.name} geometry`, async ({ page }) => {
      // This batch visits every permitted route; individual route assertions retain their own deadlines.
      test.setTimeout(45_000)
      for (const route of geometryRouteIdsFor(user)) await test.step(route, async () => {
        await page.setViewportSize(viewport)
        await page.addInitScript(themeName => localStorage.setItem('robopark-theme', themeName), theme)
        await openRouteFixture(page, route, user)
        await settlePage(page)
        await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
        await assertRouteSemanticContracts(page, route)
        await assertResponsiveContracts(page, viewport.width)
      })
    })
  }
}

for (const mode of ['Классический'] as const) test(`${mode} separates management summary from the next panel`, async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openRouteFixture(page, 'admin', userForRole('royal'))
  await settlePage(page)

  const metrics = await page.locator('.rp-management-metrics').boundingBox()
  const panel = await page.locator('.rp-management-metrics + .panel').boundingBox()
  expect(metrics).not.toBeNull()
  expect(panel).not.toBeNull()
  expect(panel!.y - (metrics!.y + metrics!.height)).toBeGreaterThanOrEqual(16)
})

for (const mode of ['Классический'] as const) test(`${mode} keeps park selection compact and centers automatic sync status`, async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openRouteFixture(page, 'admin', userForRole('royal'))
  await settlePage(page)

  const park = await page.locator('.rp-shell__topbar .rp-shell__park-brand').boundingBox()
  const indicator = await page.locator('.rp-sync-center__trigger').boundingBox()
  const dot = await page.locator('.rp-sync-center__dot').boundingBox()
  expect(park).not.toBeNull()
  expect(indicator).not.toBeNull()
  expect(dot).not.toBeNull()
  expect(park!.width).toBeLessThan(220)
  expect(Math.abs(dot!.x + dot!.width / 2 - (indicator!.x + indicator!.width / 2))).toBeLessThanOrEqual(2)
  await page.locator('.rp-shell__park-brand').click()
  await expect(page.locator('.rp-shell__park-selector button').first()).toHaveCSS('border-radius', '12px')
})

test('compact sync states stay visible and centered at 390px', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  await page.setContent(`
    <link rel="stylesheet" href="/src/design-system/styles/tokens.css">
    <link rel="stylesheet" href="/src/pwa/SyncCenter.css">
    ${['idle', 'syncing', 'offline', 'pending', 'attention'].map(state => `
      <div class="rp-sync-center">
        <button aria-label="Состояние: ${state}" class="rp-sync-center__trigger" type="button">
          <span aria-hidden="true" class="rp-sync-center__dot is-${state}"></span>
          <span>Автосинхронизация: ${state}</span><strong>2</strong>
        </button>
      </div>`).join('')}
  `)
  for (const state of ['idle', 'syncing', 'offline', 'pending', 'attention']) {
    const trigger = await page.getByRole('button', { name: `Состояние: ${state}` }).boundingBox()
    const dot = await page.locator(`.rp-sync-center__dot.is-${state}`).boundingBox()
    expect(trigger).not.toBeNull()
    expect(dot).not.toBeNull()
    expect(trigger!.width).toBeGreaterThanOrEqual(44)
    expect(trigger!.height).toBeGreaterThanOrEqual(44)
    expect(Math.abs(dot!.x + dot!.width / 2 - (trigger!.x + trigger!.width / 2))).toBeLessThanOrEqual(1)
    expect(Math.abs(dot!.y + dot!.height / 2 - (trigger!.y + trigger!.height / 2))).toBeLessThanOrEqual(1)
  }
})

for (const width of [320, 390, 1440] as const) test(`Classic keeps task and related-work navigation fully visible at ${width}px`, async ({ page }, testInfo) => {
  await page.setViewportSize({ width, height: 844 })
  await openRouteFixture(page, 'work-issue', userForRole('mechanic'))
  await settlePage(page)
  const tabs = page.getByRole('tablist', { name: 'Разделы задачи' })
  await expect(tabs.getByRole('tab')).toHaveCount(4)
  await expect(page.getByRole('tablist', { name: 'Другие задачи робота' })).toHaveCount(0)
  const box = await tabs.boundingBox()
  expect(box).not.toBeNull()
  expect(box!.height).toBeLessThanOrEqual(width <= 390 ? 50 : 100)
  const taskPanel = page.locator('.rp-work-detail-pane > .rp-panel')
  const panelPadding = await taskPanel.evaluate(element => parseFloat(getComputedStyle(element).paddingInlineStart))
  expect(panelPadding).toBeLessThanOrEqual(8)
  if (width <= 390) {
    const panelBox = await taskPanel.boundingBox()
    expect(panelBox).not.toBeNull()
    expect(panelBox!.x).toBeLessThanOrEqual(12)
    expect(panelBox!.x + panelBox!.width).toBeGreaterThanOrEqual(width - 12)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
    await expect(taskPanel.locator('.rp-panel__title')).toHaveText('Детали задачи')
    await expect(taskPanel.locator('.rp-panel__collapse')).toHaveCount(0)
  }
  for (const tab of await tabs.getByRole('tab').all()) {
    const item = await tab.boundingBox()
    expect(item).not.toBeNull()
    expect(item!.x).toBeGreaterThanOrEqual(box!.x - 1)
    expect(item!.x + item!.width).toBeLessThanOrEqual(box!.x + box!.width + 1)
  }
  if (width <= 390) {
    const labels = await Promise.all((await tabs.locator('.rp-tabs__tab > span:first-child').all()).map(label => label.boundingBox()))
    for (let index = 1; index < labels.length; index++) {
      expect(labels[index]!.x - labels[index - 1]!.x - labels[index - 1]!.width).toBeGreaterThanOrEqual(4)
    }
  }
  await page.screenshot({ path: testInfo.outputPath(`work-unified-tabs-task-${width}.png`), animations: 'disabled' })
  await tabs.getByRole('tab', { name: 'Закрытые задачи' }).click()
  await expect(tabs.getByRole('tab', { name: 'Закрытые задачи' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('heading', { name: /Закрытые задачи робота/ })).toBeVisible()
  await expect(page.locator('.rp-work-related-tasks .rp-loading-state')).toHaveCount(0)
  await page.screenshot({ path: testInfo.outputPath(`work-unified-tabs-${width}.png`), animations: 'disabled' })
})

test('selected task stays above the fold at 1024px instead of following the full queue', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 800 })
  await openRouteFixture(page, 'work-issue', userForRole('mechanic'))
  await settlePage(page)
  await expect(page.getByRole('button', { name: 'Назад к списку' })).toBeVisible()
  await expect(page.locator('.rp-master-detail__list')).toBeHidden()
  await expect(page.locator('.rp-work-filters')).toBeHidden()
  const task = await page.locator('.rp-work-detail-pane > .rp-panel').boundingBox()
  expect(task).not.toBeNull()
  expect(task!.y).toBeLessThan(220)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(1024)
})

test('selected task and queue share the desktop work area without overlap', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openRouteFixture(page, 'work-issue', userForRole('mechanic'))
  await settlePage(page)
  const workArea = await page.locator('.rp-workbench').boundingBox()
  const task = await page.locator('.rp-master-detail__detail').boundingBox()
  const queue = page.locator('.rp-workbench .rp-master-detail__list')
  await expect(queue).toBeVisible()
  const queueBox = await queue.boundingBox()
  expect(workArea && task && queueBox).toBeTruthy()
  expect(queueBox!.x + queueBox!.width).toBeLessThan(task!.x)
  expect(task!.x + task!.width).toBeLessThanOrEqual(workArea!.x + workArea!.width + 1)
  expect(task!.width).toBeGreaterThan(queueBox!.width)
})

test('remaining SLA stays readable on one line inside a 320px task card', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 900 })
  await installOperational(page, { issue: {
    ...issue,
    claim: { park_id: 7 },
    workflow: {
      owner: { display: 'Механик смены', login: 'mechanic-e2e' },
      review_state: null,
      display_status: 'in_progress',
      sync_state: 'synced',
      queued_at: '2026-09-01T06:00:00Z',
      queued_at_source: 'tracker_history',
      has_current_cycle_comment: true,
    },
    queued_at: '2026-09-01T06:00:00Z',
    sla_deadline: '2026-09-01T11:00:00Z',
    sla_source: 'status_history',
  } })
  await page.goto('/work/ROBOPARK-42?park=7')
  const label = page.locator('.rp-work-detail-pane').getByRole('status').filter({ hasText: /^SLA:/ })
  await expect(label).toHaveText('SLA: 0:00 · Просрочено')
  await settlePage(page)
  const lineHeight = await label.evaluate(element => parseFloat(getComputedStyle(element).lineHeight))
  const box = await label.boundingBox()
  expect(box).not.toBeNull()
  expect(box!.height).toBeLessThanOrEqual(lineHeight + 1)
})

test('primary task action stays clear of phone navigation without scrolling', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 900 })
  await openRouteFixture(page, 'work-issue', userForRole('mechanic'))
  await settlePage(page)
  const action = await page.getByRole('button', { name: 'Передать на проверку' }).boundingBox()
  const navigation = await page.locator('.rp-shell__bottom-nav').boundingBox()
  expect(action && navigation).toBeTruthy()
  expect(action!.y + action!.height).toBeLessThanOrEqual(navigation!.y - 8)
})

test('task support actions stay visible together on a narrow phone', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 844 })
  await openRouteFixture(page, 'work-issue', userForRole('mechanic'))
  await settlePage(page)
  const parts = await page.getByRole('button', { name: 'Списать запчасть' }).boundingBox()
  const handoff = await page.getByRole('button', { name: 'Передать смену' }).boundingBox()
  const navigation = await page.locator('.rp-shell__bottom-nav').boundingBox()
  expect(parts && handoff && navigation).toBeTruthy()
  expect(Math.abs(parts!.y - handoff!.y)).toBeLessThanOrEqual(1)
  expect(parts!.y + parts!.height).toBeLessThanOrEqual(navigation!.y - 8)
  expect(handoff!.y + handoff!.height).toBeLessThanOrEqual(navigation!.y - 8)
})

test('phone task action surfaces have breathing room between their borders', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 })
  await openRouteFixture(page, 'work-issue', userForRole('mechanic'))
  await settlePage(page)
  const primary = await page.locator('.rp-work-detail-pane .issue-actions').first().boundingBox()
  const support = await page.getByRole('group', { name: 'Дополнительные разделы задачи' }).boundingBox()
  const history = await page.getByRole('button', { name: 'История и сообщения' }).boundingBox()
  expect(primary && support && history).toBeTruthy()
  expect(support!.y - (primary!.y + primary!.height)).toBeGreaterThanOrEqual(8)
  expect(history!.y - (support!.y + support!.height)).toBeGreaterThanOrEqual(8)
})

test('operator review decision stays above phone navigation', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 900 })
  await installOperational(page, { role: 'operator', issue: { ...issue, claim: { park_id: 7 }, workflow: {
    owner: { display: 'Механик смены', login: 'mechanic-e2e' },
    review_state: 'pending', display_status: 'review', sync_state: 'saved', has_current_cycle_comment: true,
  } } })
  await page.goto('/work/ROBOPARK-42?park=7')
  await settlePage(page)
  await expect(page.getByRole('button', { name: 'Передать смену' })).toHaveCount(0)
  const approve = await page.getByRole('button', { name: 'Принять и закрыть' }).boundingBox()
  const navigation = await page.locator('.rp-shell__bottom-nav').boundingBox()
  expect(approve && navigation).toBeTruthy()
  expect(approve!.y + approve!.height).toBeLessThanOrEqual(navigation!.y - 8)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.screenshot({ path: testInfo.outputPath('operator-review-card-390.png'), animations: 'disabled' })
})

for (const mode of ['Классический'] as const) test(`${mode} keeps the task-management action compact on desktop`, async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openRouteFixture(page, 'work-issue', userForRole('royal'))
  await settlePage(page)
  const control = page.getByRole('button', { name: 'Скрыть задачу' })
  await expect(control).toBeVisible()
  const button = await control.boundingBox()
  const section = await page.getByRole('region', { name: 'Управление задачей' }).boundingBox()
  expect(button).not.toBeNull()
  expect(section).not.toBeNull()
  expect(button!.width).toBeLessThan(section!.width / 2)
})

test('Classic shows roles across the workspace until a role is selected', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openRouteFixture(page, 'admin', userForRole('royal'))
  await page.goto('/admin/roles?park=7')
  await settlePage(page)
  const layout = page.locator('.admin-roles .rp-master-detail')
  await expect(layout).toHaveAttribute('data-detail-empty', 'true')
  await expect(layout.locator('.rp-master-detail__detail')).toHaveCount(0)
  const list = await layout.locator('.rp-master-detail__list').boundingBox()
  expect(list!.width).toBeGreaterThan(900)
  await page.getByRole('button', { name: /Открыть роль/ }).first().click()
  await expect(layout).toHaveAttribute('data-detail-empty', 'false')
  await expect(layout.locator('.rp-master-detail__detail')).toBeVisible()
})

for (const mode of ['Классический'] as const) test(`${mode} separates server health panels`, async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openRouteFixture(page, 'admin-settings', userForRole('royal'), { routes: [{
    method: 'GET', path: '/api/admin/health', handler: () => ({ json: {
      sampled_at: 1789980000, database: 'ok', window_seconds: 300,
      disk: { total_bytes: 10000000000, free_bytes: 7000000000 },
      memory: { total_bytes: 8000000000, available_bytes: 5000000000, container_limit_bytes: 2000000000, container_used_bytes: 300000000 },
      backup: { verified_at: null, overdue: false, last_attempt_failed: false }, requests: {},
    } }),
  }] })
  await page.goto('/admin/settings?park=7&tab=health')
  const first = await page.locator('.stack > .panel').nth(0).boundingBox()
  const second = await page.locator('.stack > .panel').nth(1).boundingBox()
  expect(first).not.toBeNull()
  expect(second).not.toBeNull()
  expect(second!.y - (first!.y + first!.height)).toBeGreaterThanOrEqual(16)
})

test('appearance choices use compact radio circles with full-size clickable rows', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openRouteFixture(page, 'admin', userForRole('royal'))
  await page.getByRole('button', { name: 'Ещё', exact: true }).click()
  const choice = page.locator('.rp-shell__accent-group label').first()
  const circle = await choice.locator('.rp-shell__accent-swatch').boundingBox()
  const row = await choice.boundingBox()
  expect(circle).not.toBeNull()
  expect(row).not.toBeNull()
  expect(circle!.width).toBeLessThanOrEqual(24)
  expect(circle!.height).toBeLessThanOrEqual(24)
  expect(row!.height).toBeGreaterThanOrEqual(44)
  await choice.click()
  await expect(choice.getByRole('radio')).toBeChecked()
})

for (const mode of ['Классический'] as const) test(`${mode} separates robot-check search and result card`, async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openRouteFixture(page, 'admin-robot-check', userForRole('royal'))
  await settlePage(page)
  await page.getByRole('tab', { name: 'Разделы и поля', exact: true }).click()
  const searchBox = page.getByRole('searchbox', { name: 'Поиск разделов' })
  const cards = page.locator('.card-list').first()
  await expect(searchBox).toBeVisible()
  await expect(cards).toBeVisible()
  const search = await searchBox.boundingBox()
  const list = await cards.boundingBox()
  expect(search).not.toBeNull()
  expect(list).not.toBeNull()
  expect(list!.y - (search!.y + search!.height)).toBeGreaterThanOrEqual(12)
})

for (const route of ['admin', 'admin-roles', 'admin-robot-check', 'reports', 'campaigns', 'work-issue'] as const) {
  for (const mode of ['Классический'] as const) test(`${mode} ${route} keeps a usable desktop width`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.addInitScript(() => localStorage.setItem('robopark-theme', 'dark'))
    await openRouteFixture(page, route, userForRole(route === 'work-issue' ? 'mechanic' : 'royal'))
    await settlePage(page)
    await assertResponsiveContracts(page, 1440)
  })
}

for (const route of ['admin-roles', 'admin-robot-check', 'reports', 'work-issue'] as const) {
  for (const mode of ['Классический'] as const) test(`${mode} ${route} fits a narrow light viewport`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.addInitScript(() => localStorage.setItem('robopark-theme', 'light'))
    await openRouteFixture(page, route, userForRole(route === 'work-issue' ? 'mechanic' : 'royal'))
    await settlePage(page)
    await assertResponsiveContracts(page, 390)
  })
}
