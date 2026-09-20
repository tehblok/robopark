import { defineConfig, devices } from '@playwright/test'
import { fileURLToPath } from 'node:url'

const port = Number(process.env.PLAYWRIGHT_PWA_PORT ?? 4174)
const baseURL = `http://127.0.0.1:${port}`
const webRoot = fileURLToPath(new URL('.', import.meta.url))

export default defineConfig({
  testDir: './e2e-production',
  testMatch: '**/*.spec.ts',
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL,
    serviceWorkers: 'allow',
    trace: 'on-first-retry',
  },
  webServer: {
    command: `"${process.execPath}" node_modules/vite/bin/vite.js preview --host 127.0.0.1 --port ${port} --strictPort`,
    cwd: webRoot,
    url: baseURL,
    reuseExistingServer: false,
  },
  projects: [
    {
      name: 'chromium-pwa',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
