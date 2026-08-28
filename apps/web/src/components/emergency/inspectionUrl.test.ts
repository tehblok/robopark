import { describe, expect, it } from 'vitest'
import {
  buildInspectionParams,
  inspectionParamsEqual,
  parseInspectionParams,
} from './inspectionUrl'

describe('inspectionUrl', () => {
  it('reads robot query and open tab from the URL', () => {
    expect(parseInspectionParams(new URLSearchParams('q=a1427&tab=wheels'))).toEqual({
      q: 'a1427',
      tab: 'wheels',
    })
    expect(parseInspectionParams(new URLSearchParams('robot=447'))).toEqual({
      q: '447',
      tab: 'map',
    })
  })

  it('omits the default map tab so F5 stays on a stable URL', () => {
    expect(buildInspectionParams('a1427', 'map').toString()).toBe('q=a1427')
    expect(buildInspectionParams('a1427', 'wheels').toString()).toBe('q=a1427&tab=wheels')
    expect(buildInspectionParams('', 'wheels').toString()).toBe('tab=wheels')
  })

  it('compares query strings', () => {
    expect(
      inspectionParamsEqual(buildInspectionParams('a1', 'map'), new URLSearchParams('q=a1')),
    ).toBe(true)
  })
})
