import { describe, expect, it } from 'vitest'
import { parseRobotReference } from './robotReference'

describe('parseRobotReference', () => {
  it.each([
    ['447', '447'],
    ['a1555', 'a1555'],
    ['YASADR00000001975', 'YASADR00000001975'],
    ['/robots/YASADR00000001975', 'YASADR00000001975'],
    ['https://robopark.example/robots/447/check?tab=map', '447'],
    ['/emergency?q=1555&tab=wheels', '1555'],
    ['/emergency?robot=1666', '1666'],
  ])('extracts %s', (raw, expected) => {
    expect(parseRobotReference(raw)).toBe(expected)
  })

  it.each([
    '',
    '/robots',
    '/emergency?tab=map',
    'https://example.test/not-a-robot',
    'robot',
    '/robots/%E0%A4%A',
  ])('rejects %s', (raw) => {
    expect(parseRobotReference(raw)).toBeNull()
  })
})
