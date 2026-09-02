import AxeBuilder from '@axe-core/playwright'
import { expect, type Page } from '@playwright/test'

const WCAG_AA_TAGS = new Set([
  'wcag2a', 'wcag2aa',
  'wcag21a', 'wcag21aa',
  'wcag22a', 'wcag22aa',
])

export function wcagAaViolations<T extends { tags: string[] }>(violations: T[]): T[] {
  return violations.filter((item) => item.tags.some((tag) => WCAG_AA_TAGS.has(tag)))
}

export async function assertNoSeriousA11yViolations(page: Page): Promise<void> {
  const result = await new AxeBuilder({ page }).analyze()
  const blocking = wcagAaViolations(result.violations)
  expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([])
}
