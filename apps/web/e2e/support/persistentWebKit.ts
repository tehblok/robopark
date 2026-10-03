import { rm } from 'node:fs/promises'
import { expect, test as base, type BrowserContextOptions } from '@playwright/test'

type InternalContextOptions = { _combinedContextOptions: BrowserContextOptions }
const configuredBase = base.extend<InternalContextOptions>({})

/**
 * Playwright's ephemeral WebKit context cannot persist Blob/File values in
 * IndexedDB. A persistent profile matches normal Safari storage semantics.
 * https://github.com/microsoft/playwright/issues/42795
 * https://bugs.webkit.org/show_bug.cgi?id=156347
 * `_combinedContextOptions` is an internal contract of the pinned Playwright
 * version; cross-browser and PWA gates must revalidate it after upgrades.
 */
export const test = configuredBase.extend({
  context: async ({ browser, browserName, playwright, _combinedContextOptions }, provide, testInfo) => {
    if (browserName !== 'webkit') {
      const context = await browser.newContext(_combinedContextOptions)
      try { await provide(context) } finally { await context.close() }
      return
    }

    const profile = testInfo.outputPath('webkit-profile')
    const context = await playwright.webkit.launchPersistentContext(profile, _combinedContextOptions)
    try {
      await provide(context)
    } finally {
      await context.close()
      await rm(profile, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 })
    }
  },
  page: async ({ browserName, context }, provide) => {
    const page = browserName === 'webkit' ? context.pages()[0] : await context.newPage()
    if (!page) throw new Error('Persistent WebKit context did not create its initial page')
    try { await provide(page) } finally { await page.close() }
  },
})

export { expect }
