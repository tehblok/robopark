import { defineConfig } from '../../../apps/web/node_modules/@playwright/test/index.mjs'
import { fileURLToPath } from 'node:url'
export default defineConfig({
  testDir: '.', testMatch: 'preview.spec.ts', workers: 1,
  outputDir: '/private/tmp/robopark-ux-comparison',
  use: { baseURL: 'http://127.0.0.1:4177', browserName: 'chromium' },
  webServer: {
    command: `"${process.execPath}" node_modules/vite/bin/vite.js --host 127.0.0.1 --port 4177 --strictPort`,
    cwd: fileURLToPath(new URL('../../../apps/web', import.meta.url)),
    url: 'http://127.0.0.1:4177', reuseExistingServer: false,
  },
})
