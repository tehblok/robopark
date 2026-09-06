import { expect, it } from 'vitest'
import { pollDelayAfterFailure } from './polling'
it('bounds consecutive failure delays', () => {
  expect([0, 1, 2, 3, 4, 5, 99].map(pollDelayAfterFailure)).toEqual([10000, 20000, 40000, 60000, 120000, 120000, 120000])
})
