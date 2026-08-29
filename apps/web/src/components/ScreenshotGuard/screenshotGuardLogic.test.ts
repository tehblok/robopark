import { describe, expect, it } from 'vitest'
import {
  buildWatermarkLabel,
  isProtectedMediaTarget,
  isScreenshotShortcut,
  shouldBlockProtectedAction,
} from './screenshotGuardLogic'

function keyEvent(init: KeyboardEventInit): KeyboardEvent {
  return new KeyboardEvent('keydown', init)
}

describe('isScreenshotShortcut', () => {
  it('detects PrintScreen', () => {
    expect(isScreenshotShortcut(keyEvent({ key: 'PrintScreen' }))).toBe(true)
    expect(isScreenshotShortcut(keyEvent({ code: 'PrintScreen', key: 'Unidentified' }))).toBe(
      true,
    )
  })

  it('detects Windows snipping shortcut', () => {
    expect(
      isScreenshotShortcut(keyEvent({ key: 's', shiftKey: true, metaKey: true })),
    ).toBe(true)
  })

  it('detects macOS region screenshot', () => {
    expect(
      isScreenshotShortcut(keyEvent({ key: '4', shiftKey: true, metaKey: true })),
    ).toBe(true)
  })

  it('ignores ordinary typing', () => {
    expect(isScreenshotShortcut(keyEvent({ key: 'a' }))).toBe(false)
    expect(isScreenshotShortcut(keyEvent({ key: 's', ctrlKey: true }))).toBe(false)
  })

  it('does not treat Ctrl+Shift+S as a screenshot', () => {
    expect(
      isScreenshotShortcut(keyEvent({ key: 's', shiftKey: true, ctrlKey: true })),
    ).toBe(false)
  })

  it('still treats Meta+Shift+S as a snipping shortcut', () => {
    expect(
      isScreenshotShortcut(keyEvent({ key: 's', shiftKey: true, metaKey: true })),
    ).toBe(true)
  })
})

describe('buildWatermarkLabel', () => {
  it('includes username and user id', () => {
    expect(buildWatermarkLabel('operator1', 42)).toContain('operator1')
    expect(buildWatermarkLabel('operator1', 42)).toContain('#42')
  })
})

describe('shouldBlockProtectedAction', () => {
  it('allows clipboard actions inside inputs', () => {
    const input = document.createElement('input')
    document.body.appendChild(input)
    const event = new Event('copy', { bubbles: true })
    Object.defineProperty(event, 'target', { value: input })
    expect(shouldBlockProtectedAction(event)).toBe(false)
    input.remove()
  })

  it('blocks clipboard actions on page content', () => {
    const div = document.createElement('div')
    document.body.appendChild(div)
    const event = new Event('copy', { bubbles: true })
    Object.defineProperty(event, 'target', { value: div })
    expect(shouldBlockProtectedAction(event)).toBe(true)
    div.remove()
  })
})

describe('isProtectedMediaTarget', () => {
  it('detects images', () => {
    const img = document.createElement('img')
    expect(isProtectedMediaTarget(img)).toBe(true)
  })
})
