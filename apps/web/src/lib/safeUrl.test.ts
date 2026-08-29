import { describe, expect, it } from 'vitest'
import { safeHttpUrl } from './safeUrl'

describe('safeHttpUrl', () => {
  it('allows http and https', () => {
    expect(safeHttpUrl('https://st.yandex-team.ru/X-1')).toBe('https://st.yandex-team.ru/X-1')
    expect(safeHttpUrl('http://example.com/a')).toBe('http://example.com/a')
  })

  it('blocks non-http schemes', () => {
    expect(safeHttpUrl('javascript:alert(1)')).toBeNull()
    expect(safeHttpUrl('data:text/html,hi')).toBeNull()
  })

  it('returns null for empty or invalid', () => {
    expect(safeHttpUrl('')).toBeNull()
    expect(safeHttpUrl(null)).toBeNull()
    expect(safeHttpUrl('not a url')).toBeNull()
  })
})
