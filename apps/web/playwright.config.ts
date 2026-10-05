import { defineConfig, devices } from '@playwright/test'
import { fileURLToPath } from 'node:url'

const port = Number(process.env.PLAYWRIGHT_PORT ?? 4173)
const baseURL = `http://127.0.0.1:${port}`
const webRoot = fileURLToPath(new URL('.', import.meta.url))
const soakOnly = process.env.ROBOPARK_E2E_SUITE === 'soak'

export default defineConfig({
  testDir: './e2e',
  testMatch: soakOnly ? '**/soak.spec.ts' : '**/*.spec.ts',
  testIgnore: soakOnly ? [] : ['**/soak.spec.ts'],
  retries: process.env.CI ? 2 : 0,
  expect: {
    toHaveScreenshot: {
      animations: 'disabled',
    },
  },
  snapshotPathTemplate: '{testDir}/{testFilePath}-snapshots/{arg}{ext}',
  use: {
    baseURL,
    trace: 'retain-on-failure',
  },
  webServer: {
    command: `"${process.execPath}" node_modules/vite/bin/vite.js --host 127.0.0.1 --port ${port} --strictPort`,
    cwd: webRoot,
    url: baseURL,
    reuseExistingServer: false,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
