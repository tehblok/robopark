import { expect, test, type Page } from '@playwright/test'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'
import { installOperational, settlePage, snapshot } from './fixtures'

const widths = [320, 390, 768, 1024, 1440] as const
const themes = ['light', 'dark'] as const
const states = [
  { name: 'overview', path: '/overview?park=7', ready: '.rp-overview' },
  { name: 'work', path: '/work/ROBOPARK-42?park=7&status=open&sort=newest&page=2', ready: '.issue-actions' },
  { name: 'robots', path: '/robots?park=7', ready: '.rp-robots-search-panel' },
  { name: 'robot-check', path: `/robots/${snapshot.vin}/check?park=7&tab=scheme`, ready: '.rp-check-photo-frame img' },
] as const

async function assertResponsiveContracts(page: Page, width: number) {
  const overflow = await page.evaluate(() => ({
    amount: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    offenders: Array.from(document.querySelectorAll('body *'))
      .filter((element) => element.getBoundingClientRect().right + window.scrollX > document.documentElement.clientWidth + 1)
      .map((element) => `${element.tagName.toLowerCase()}.${element.className}: ${Math.round(element.getBoundingClientRect().right + window.scrollX)}`)
      .slice(0, 12),
    layout: ['.rp-app-shell', '.rp-shell__sidebar', '.rp-shell__main-column', '.rp-shell__content', '.rp-page-layout', '.rp-workbench', '.rp-work-list-pane', '.rp-work-detail-pane']
      .map((selector) => {
        const element = document.querySelector(selector)
        if (!element) return `${selector}: missing`
        const box = element.getBoundingClientRect()
        const style = getComputedStyle(element)
        return `${selector}: x=${Math.round(box.x + window.scrollX)} w=${Math.round(box.width)} scroll=${element.scrollWidth}/${element.clientWidth} cols=${style.gridTemplateColumns}`
      }),
  }))
  expect(overflow.amount, `horizontal overflow: ${overflow.offenders.join(', ')}; layout: ${overflow.layout.join('; ')}`).toBeLessThanOrEqual(0)
  const violations = await page.evaluate(width => {
    const failures: string[] = []
    const visible = (element: Element) => {
      const style = getComputedStyle(element)
      return element.getClientRects().length > 0 && style.visibility !== 'hidden' && style.display !== 'none'
    }
    const name = (element: Element) => `${element.tagName.toLowerCase()}#${element.id}.${element.className} ${(element.getAttribute('aria-label') || element.textContent || '').trim().slice(0, 70)}`
    for (const element of document.querySelectorAll('body *')) {
      if (!visible(element) || element.closest('[aria-hidden="true"],svg,script,style,option')) continue
      const control = element.matches('button,a,input,select,textarea')
      const hasText = Array.from(element.childNodes).some(node => node.nodeType === Node.TEXT_NODE && node.textContent?.trim())
      if (!control && !hasText) continue
      // Only an explicit semantic supplementary marker can opt into the 12px exception.
      const minimum = element.closest('[data-supplementary="true"]') ? 12 : 14
      const fontSize = parseFloat(getComputedStyle(element).fontSize)
      if (fontSize < minimum) failures.push(`font ${fontSize}<${minimum}: ${name(element)}`)
      if (width <= 899 && element.matches('input:not([type="checkbox"]):not([type="radio"]):not([type="file"]),select,textarea') && fontSize < 16) failures.push(`input font ${fontSize}<16: ${name(element)}`)
    }
    if (width <= 899) {
      const navigation = document.querySelector('.rp-shell__bottom-nav')!
      for (const label of navigation.querySelectorAll('.rp-shell__nav-label')) {
        const box = label.getBoundingClientRect()
        const control = label.closest('a,button')!.getBoundingClientRect()
        const icon = label.parentElement!.querySelector('svg')!.getBoundingClientRect()
        if (!visible(label) || box.height <= 0 || box.width <= 0) failures.push(`hidden navigation caption: ${name(label)}`)
        if (box.top < icon.bottom - 1 || box.left < control.left - 1 || box.right > control.right + 1 || box.bottom > control.bottom + 1) failures.push(`navigation caption outside its control or above icon: ${name(label)}`)
      }
    }
    if (width === 320 || width === 390) {
      for (const element of document.querySelectorAll('button,a,input,select,textarea')) {
        if (!visible(element)) continue
        const target = element.matches('input[type="checkbox"],input[type="radio"]')
          ? (element as HTMLInputElement).labels?.[0] : element
        if (!target) { failures.push(`missing associated label: ${name(element)}`); continue }
        const box = target.getBoundingClientRect()
        if (box.width < 43.99 || box.height < 43.99) failures.push(`target ${box.width}x${box.height}: ${name(element)}`)
      }
    }
    return failures
  }, width)
  expect(violations).toEqual([])
}

async function assertPhotoGeometry(page: Page) {
  const photo = page.locator('.rp-check-photo-frame img')
  await expect.poll(() => photo.evaluate((element: HTMLImageElement) => element.naturalWidth)).toBe(2269)
  const image = await photo.boundingBox()
  const markers = page.locator('.rp-check-wheel')
  await expect(markers).toHaveCount(6)
  const expected = [[.202, .215], [.202, .465], [.202, .715], [.798, .215], [.798, .465], [.798, .715]]
  const boxes = await markers.evaluateAll(elements => elements.map(element => {
    const box = element.getBoundingClientRect()
    return { x: box.x, y: box.y, width: box.width, height: box.height }
  }))
  for (const [index, box] of boxes.entries()) {
    expect(Math.abs((box.x + box.width / 2 - image!.x) / image!.width - expected[index][0])).toBeLessThan(.005)
    expect(Math.abs((box.y + box.height / 2 - image!.y) / image!.height - expected[index][1])).toBeLessThan(.005)
    for (const other of boxes.slice(index + 1)) {
      expect(box.x + box.width <= other.x || other.x + other.width <= box.x || box.y + box.height <= other.y || other.y + other.height <= box.y).toBe(true)
    }
  }
  await expect(page.getByRole('button', { name: 'Переднее левое колесо: неисправность', exact: true })).toBeVisible()
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
      await expect(page.locator('.rp-overview-flow')).toBeInViewport()
      await expect(page.getByRole('heading', { name: 'Статусы задач' })).toBeInViewport()
      await expect(page.getByRole('heading', { name: 'Очередь внимания' })).toBeInViewport()
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
