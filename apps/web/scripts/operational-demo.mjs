import { chromium } from '@playwright/test'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'

const host = '127.0.0.1'
const smoke = process.argv.includes('--smoke')
const portArgument = process.argv.find((argument) => argument.startsWith('--port='))
const port = Number(portArgument?.slice('--port='.length) ?? 4174)
if (!Number.isInteger(port) || port < 1 || port > 65_535) {
  throw new Error('Use --port=<1..65535>')
}

const server = await createServer({
  configFile: fileURLToPath(new URL('../vite.config.ts', import.meta.url)),
  server: { host, port, strictPort: true },
})

let browser
let closing = false
async function close() {
  if (closing) return
  closing = true
  await browser?.close()
  await server.close()
}

try {
  await server.listen()
  const fixtures = await server.ssrLoadModule('/e2e/operational/fixtures.ts')
  const { installMockApi } = await server.ssrLoadModule('/e2e/support/mockApi.ts')
  browser = await chromium.launch({ headless: smoke })
  const baseURL = `http://${host}:${port}`
  const context = await browser.newContext({ baseURL, viewport: { width: 1280, height: 900 } })

  for (const role of fixtures.roles) {
    console.log(`Opening Operations fixture: ${role}`)
    const page = await context.newPage()
    const browserErrors = []
    page.on('pageerror', (error) => browserErrors.push(error.message))
    page.on('console', (message) => {
      if (message.type() === 'error') browserErrors.push(message.text())
    })
    page.on('response', (response) => {
      if (response.status() >= 400) browserErrors.push(`${response.status()} ${response.url()}`)
    })
    const user = fixtures.userForRole(role)
    await page.clock.setFixedTime(new Date('2026-09-02T09:05:00Z'))
    await page.route(/^https?:\/\/(?!localhost(?=[:/])|127\.0\.0\.1(?=[:/]))/, (route) => route.abort())
    await installMockApi(page, {
      user,
      parks: user.parks,
      routes: fixtures.operationalRoutes({ user }),
    })
    await page.goto('/overview?park=7')
    try {
      await page.getByRole('heading', { name: 'Смена / Обзор' }).waitFor({ timeout: 10_000 })
      await page.getByRole('link', { name: 'Открыть задачу ROBOPARK-42' }).waitFor()
      if (smoke) {
        await page.getByRole('link', { name: 'Открыть задачу ROBOPARK-42' }).click()
        await page.getByRole('heading', { name: 'Работа' }).waitFor({ timeout: 10_000 })
      }
      if (smoke) await page.waitForTimeout(100)
      if (browserErrors.length) throw new Error(`browser errors: ${browserErrors.join(' | ')}`)
    } catch (error) {
      const rendered = (await page.locator('body').innerText()).replaceAll(/\s+/g, ' ').slice(0, 500)
      throw new Error(`Operations did not render for ${role} at ${page.url()}: ${rendered}; browser errors: ${browserErrors.join(' | ') || 'none'}`, { cause: error })
    }
    console.log(`Operations fixture ready: ${role}`)
    if (smoke) await page.close()
  }

  if (smoke) {
    console.log('Fixture-only smoke complete: Operations rendered for every role.')
  } else {
    console.log(`Fixture-only demo: ${baseURL}/overview?park=7`)
    console.log('Five tabs are open (mechanic, operator, driver, admin, royal). Press Ctrl+C to stop.')
    await new Promise((resolve) => {
      process.once('SIGINT', resolve)
      process.once('SIGTERM', resolve)
      browser.once('disconnected', resolve)
    })
  }
} finally {
  await close()
}
