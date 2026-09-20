import { expect, it } from 'vitest'
import { recentRobotLabels } from './RobotsPage'

it('does not repeat a VIN used as the recent robot query', () => {
  expect(recentRobotLabels({ query: 'YASADR00000000447', vin: 'YASADR00000000447', openedAt: 1 }))
    .toEqual({ primary: 'Робот 447', secondary: null })
  expect(recentRobotLabels({ query: '447', vin: 'YASADR00000000447', openedAt: 1 }))
    .toEqual({ primary: '447', secondary: 'YASADR00000000447' })
})
