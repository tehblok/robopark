import { describe, expect, it } from 'vitest'
import { wcagAaViolations } from './assertA11y'

type SyntheticViolation = {
  id: string
  impact?: 'minor' | 'moderate'
  tags: string[]
}

describe('wcagAaViolations', () => {
  it('blocks every WCAG A/AA-tagged violation independent of impact', () => {
    const moderateWcag = { id: 'color-contrast', impact: 'moderate', tags: ['wcag2aa'] } as const
    const noImpactWcag22 = { id: 'target-size', tags: ['wcag22aa'] } as const
    const bestPracticeOnly = [
      { id: 'region', impact: 'minor', tags: ['best-practice'] },
      { id: 'landmark-one-main', impact: 'moderate', tags: ['best-practice'] },
    ] as const

    const violations: SyntheticViolation[] = [moderateWcag, noImpactWcag22, ...bestPracticeOnly]

    expect(wcagAaViolations(violations)).toEqual([moderateWcag, noImpactWcag22])
  })
})
