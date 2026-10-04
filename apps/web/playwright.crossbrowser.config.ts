import { defineConfig, devices } from '@playwright/test'
import base from './playwright.config'

// Pixel baselines belong to the pinned Chromium visual suite. This gate checks
// user-visible behavior on all three engines with the same assertions.
export default defineConfig(base, {
  testMatch: [
    '**/assistant.spec.ts',
    '**/mock-api-contract.spec.ts',
    '**/operational/{work,work-tabs,task-lifecycle,task-collaboration,task-robot-composition}.spec.ts',
    '**/operational/{robots,robot-qr,diagnostic-rules,diagnostic-editor,diagnostic-unknowns}.spec.ts',
    '**/operational/{inventory-workflows,schedule-workspace,admin-reports,admin-settings}.spec.ts',
    '**/operational/{interface-parity,workspace-navigation,automatic-refresh,report-photo-drafts}.spec.ts',
    '**/operational/{system-operations-ui,ops,shared-controls,analytics}.spec.ts',
  ],
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'firefox', use: { ...devices['Desktop Firefox'] } },
    { name: 'webkit', use: { ...devices['Desktop Safari'] } },
  ],
})
