import { describe, expect, it } from 'vitest'
import { isImageAttachment, splitPlatformComment, sortCommentsChronologically } from './commentChat'

describe('splitPlatformComment', () => {
  it('splits platform signature footer', () => {
    const result = splitPlatformComment('Фото\nAlpha / mech1 / op1')
    expect(result.body).toBe('Фото')
    expect(result.signature).toBe('Alpha / mech1 / op1')
  })

  it('keeps plain text intact', () => {
    const result = splitPlatformComment('plain comment')
    expect(result.body).toBe('plain comment')
    expect(result.signature).toBeNull()
  })
})

describe('isImageAttachment', () => {
  it('detects image mime', () => {
    expect(isImageAttachment({ id: '1', name: 'x.bin', mimetype: 'image/jpeg' })).toBe(true)
  })

  it('detects image extension', () => {
    expect(isImageAttachment({ id: '1', name: 'photo.heic', mimetype: null })).toBe(true)
  })
})

describe('sortCommentsChronologically', () => {
  it('sorts by created_at ascending', () => {
    const sorted = sortCommentsChronologically([
      { id: '2', text: 'b', created_at: '2026-01-02T00:00:00Z' },
      { id: '1', text: 'a', created_at: '2026-01-01T00:00:00Z' },
    ])
    expect(sorted.map((item) => item.id)).toEqual(['1', '2'])
  })
})
