import { createRequire } from 'node:module'
import { mkdir } from 'node:fs/promises'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const require = createRequire(join(here, '../../apps/web/package.json'))
const { chromium } = require('playwright')
const output = join(here, 'mockups')
await mkdir(output, { recursive: true })
const browser = await chromium.launch({ headless: true })
try {
  for (const variant of ['a', 'b', 'c']) {
    for (const [device, viewport, theme] of [
      ['desktop', { width: 1440, height: 900 }, 'light'],
      ['phone', { width: 390, height: 844 }, 'dark'],
    ]) {
      const page = await browser.newPage({ viewport, deviceScaleFactor: 1, colorScheme: theme })
      const url = new URL(pathToFileURL(join(here, 'robopark-directions.html')))
      url.searchParams.set('v', variant)
      url.searchParams.set('page', 'overview')
      url.searchParams.set('theme', theme)
      await page.goto(url.href)
      const path = join(output, `${variant}-${device}-${theme}.png`)
      if (device === 'phone') {
        await page.locator('.chooser').evaluate(element => { element.style.display = 'none' })
        await page.screenshot({ path })
      } else {
        await page.locator('.mock').screenshot({ path })
      }
      await page.close()
    }
  }
  for (const screen of ['work', 'analytics', 'settings']) {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 })
    const url = new URL(pathToFileURL(join(here, 'robopark-directions.html')))
    url.searchParams.set('v', 'a')
    url.searchParams.set('page', screen)
    url.searchParams.set('theme', 'light')
    await page.goto(url.href)
    await page.locator('.mock').screenshot({ path: join(output, `a-${screen}-desktop-light.png`) })
    await page.close()
  }
} finally {
  await browser.close()
}
