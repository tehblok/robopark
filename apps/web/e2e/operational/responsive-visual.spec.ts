import { expect, test, type Page } from '@playwright/test'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'
import { installOperational, settlePage, snapshot } from './fixtures'

const widths = [320, 390, 768, 1024, 1440] as const
const themes = ['light', 'dark'] as const
const states = [
  { name: 'overview', path: '/overview?park=7', ready: '.rp-overview-primary' },
  { name: 'work', path: '/work/ROBOPARK-42?park=7&status=open&sort=newest&page=2', ready: '.issue-actions' },
  { name: 'robots', path: '/robots?park=7', ready: '.rp-robots-search-panel' },
  { name: 'robot-check', path: `/robots/${snapshot.vin}/check?park=7&tab=scheme`, ready: '.rp-check-photo-frame img' },
] as const

async function assertResponsiveContracts(page: Page, width: number) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0)
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

for (const width of widths) for (const theme of themes) for (const state of states) {
  test(`${state.name}-${theme}-${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await page.addInitScript(theme => localStorage.setItem('robopark-theme', theme), theme)
    await installOperational(page)
    await page.goto(state.path)
    await expect(page.locator(state.ready)).toBeVisible()
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
    await settlePage(page)
    if (state.name === 'overview' && width >= 1024) {
      await expect(page.getByTestId('overview-state')).toBeInViewport()
      await expect(page.getByTestId('overview-queue')).toBeInViewport()
    }
    if (state.name === 'work') {
      await expect(page.locator('.rp-work-detail-pane')).toBeVisible()
      if (width >= 1024) {
        await expect(page.locator('.rp-work-list-pane')).toBeInViewport()
        await expect(page.locator('.rp-work-detail-pane')).toBeInViewport()
      } else await expect(page.locator('.rp-work-list-pane')).toBeHidden()
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

test('1440px 200% root text reflow preserves scope risk primary action and detail', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await installOperational(page)
  await page.goto('/overview?park=7')
  await page.evaluate(() => { document.documentElement.style.fontSize = '200%' })
  for (const id of ['overview-scope', 'overview-risk', 'overview-action']) {
    await page.getByTestId(id).scrollIntoViewIfNeeded()
    await expect(page.getByTestId(id)).toBeInViewport()
  }
  await assertResponsiveContracts(page, 1440)
  await page.getByTestId('overview-action').getByRole('link').click()
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
