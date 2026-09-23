import { readFileSync } from 'node:fs'
import { expect, it } from 'vitest'

const runtimeFiles = [
  'src/app/interface/InterfaceModeProvider.tsx',
  'src/domains/analytics/AnalyticsWorkspace.tsx',
  'src/domains/analytics/analytics.css',
  'src/domains/inventory/InventorySecondaryActions.tsx',
  'src/domains/shift/OverviewSections.tsx',
  'src/domains/work/IssueWorkbench.tsx',
]

it('keeps the surviving Classic runtime free of Interface A hooks and selectors', () => {
  const runtime = runtimeFiles.map(path => readFileSync(path, 'utf8')).join('\n')

  expect(runtime).not.toMatch(/useInterfaceMode/)
  expect(runtime).not.toMatch(/\ba-(?:analytics|overview|work)-[a-z0-9-]+\b/)
  expect(runtime).not.toMatch(/mode\s*===\s*['"]classic['"]\s*\?\s*true\s*:\s*undefined/)
})
