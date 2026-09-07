import { expect, it } from 'vitest'
import { buildRobotCheckSearch, checkTabs, parseRobotCheckTab } from './robotCheckUrl'
const sections = [{ id: 'wheels', title: 'Колёса' }, { id: 'power', title: 'Питание' }]
it('deduplicates reserved and dynamic ids and restores only valid tabs', () => {
  expect(checkTabs([...sections, sections[0], { id: 'map', title: 'Другая' }]).map(t => t.id)).toEqual(['map', 'state', 'errors', 'telemetry', 'tasks', 'history', 'scheme', 'wheels', 'power'])
  expect(parseRobotCheckTab(new URLSearchParams('tab=wheels'), sections)).toBe('wheels')
  expect(parseRobotCheckTab(new URLSearchParams('tab=secret'), sections)).toBe('map')
})
it('preserves only numeric park and nondefault tab', () => {
  expect(buildRobotCheckSearch(new URLSearchParams('park=7&source=x&tab=wheels'), 'telemetry')).toBe('?park=7&tab=telemetry')
  expect(buildRobotCheckSearch(new URLSearchParams('park=7&tab=wheels'), 'map')).toBe('?park=7')
  expect(buildRobotCheckSearch(new URLSearchParams('park=oops'), 'map')).toBe('')
})
