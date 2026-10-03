/// <reference types="node" />

import { readFileSync } from 'node:fs'
import { act, renderHook } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ThemeProvider } from './ThemeProvider'
import { useTheme } from './themeContext'
import {
  applyInitialTheme,
  applyTheme,
  readAccentPreference,
  readDensityPreference,
  readThemePreference,
  resolveDensity,
  resolveTheme,
} from './theme'

type MediaListener = (event: MediaQueryListEvent) => void

function createMediaQuery(matches: boolean) {
  const listeners = new Set<MediaListener>()
  let currentMatches = matches
  const media = {
    get matches() {
      return currentMatches
    },
    media: '',
    onchange: null,
    addEventListener(_type: string, listener: EventListenerOrEventListenerObject) {
      if (typeof listener === 'function') listeners.add(listener as MediaListener)
    },
    removeEventListener(_type: string, listener: EventListenerOrEventListenerObject) {
      if (typeof listener === 'function') listeners.delete(listener as MediaListener)
    },
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  } as MediaQueryList

  return {
    media,
    dispatch(nextMatches: boolean) {
      currentMatches = nextMatches
      listeners.forEach((listener) => listener({ matches: nextMatches } as MediaQueryListEvent))
    },
  }
}

const wrapper = ({ children }: PropsWithChildren) => (
  <ThemeProvider>{children}</ThemeProvider>
)

describe('theme', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.removeAttribute('data-theme')
    document.documentElement.removeAttribute('data-theme-changing')
    document.documentElement.removeAttribute('data-density')
    document.documentElement.removeAttribute('data-accent')
    document.documentElement.style.removeProperty('color-scheme')
    vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({
      matches: true,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }))
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('resolves system preference from prefers-color-scheme', () => {
    expect(resolveTheme('system', true)).toBe('dark')
    expect(resolveTheme('system', false)).toBe('light')
  })

  it('changes contrast tokens without animating button surfaces through unreadable colors', () => {
    const frames: FrameRequestCallback[] = []
    vi.stubGlobal('requestAnimationFrame', vi.fn(callback => { frames.push(callback); return frames.length }))
    vi.stubGlobal('cancelAnimationFrame', vi.fn())
    document.documentElement.dataset.theme = 'dark'

    applyTheme('light')

    expect(document.documentElement.dataset.theme).toBe('light')
    expect(document.documentElement.dataset.themeChanging).toBe('true')
    frames.shift()?.(0)
    expect(document.documentElement.dataset.themeChanging).toBe('true')
    frames.shift()?.(16)
    expect(document.documentElement.dataset.themeChanging).toBeUndefined()
  })

  it('falls back to system for an invalid stored value', () => {
    localStorage.setItem('robopark-theme', 'blue')
    expect(readThemePreference()).toBe('system')
  })

  it('keeps olive as the safe default and rejects an unknown stored accent', () => {
    expect(readAccentPreference()).toBe('olive')
    localStorage.setItem('robopark-accent', 'neon-green')
    expect(readAccentPreference()).toBe('olive')
  })

  it('boots the stored accent before React paints the application', () => {
    localStorage.setItem('robopark-accent', 'violet')

    applyInitialTheme()

    expect(document.documentElement.dataset.accent).toBe('violet')
    const relativeHtmlPath = '../../../index.html'
    const html = readFileSync(new URL(relativeHtmlPath, import.meta.url), 'utf8')
    const accentBootstrap = html.indexOf("localStorage.getItem('robopark-accent')")
    const application = html.indexOf('/src/main.tsx')
    expect(accentBootstrap).toBeGreaterThan(0)
    expect(application).toBeGreaterThan(accentBootstrap)
  })

  it('keeps the selected density on desktop and phone', () => {
    localStorage.setItem('robopark-density', 'compact')
    expect(readDensityPreference()).toBe('compact')
    expect(resolveDensity('compact', false)).toBe('compact')
    expect(resolveDensity('compact', true)).toBe('compact')
  })

  it('boots the resolved theme before the application module', () => {
    const relativeHtmlPath = '../../../index.html'
    const html = readFileSync(new URL(relativeHtmlPath, import.meta.url), 'utf8')
    const bootstrap = html.indexOf("localStorage.getItem('robopark-theme')")
    const application = html.indexOf('/src/main.tsx')
    expect(bootstrap).toBeGreaterThan(0)
    expect(application).toBeGreaterThan(bootstrap)
  })

  it('persists an explicit preference and applies its resolved theme', () => {
    const { result } = renderHook(() => useTheme(), { wrapper })

    act(() => result.current.setPreference('light'))

    expect(localStorage.getItem('robopark-theme')).toBe('light')
    expect(document.documentElement.dataset.theme).toBe('light')
    expect(document.documentElement.style.colorScheme).toBe('light')
  })

  it('persists an accent preference and applies it to the document', () => {
    const { result } = renderHook(() => useTheme(), { wrapper })

    act(() => result.current.setAccentPreference('blue'))

    expect(result.current.accentPreference).toBe('blue')
    expect(localStorage.getItem('robopark-accent')).toBe('blue')
    expect(document.documentElement.dataset.accent).toBe('blue')
  })

  it('tracks live system theme changes without rewriting the preference', () => {
    const themeMedia = createMediaQuery(false)
    const densityMedia = createMediaQuery(false)
    vi.stubGlobal('matchMedia', vi.fn((query: string) => (
      query === '(max-width: 899px)' ? densityMedia.media : themeMedia.media
    )))
    const { result } = renderHook(() => useTheme(), { wrapper })

    expect(result.current.preference).toBe('system')
    expect(document.documentElement.dataset.theme).toBe('light')

    act(() => themeMedia.dispatch(true))

    expect(result.current.preference).toBe('system')
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(document.documentElement.style.colorScheme).toBe('dark')
    expect(localStorage.getItem('robopark-theme')).toBeNull()
  })

  it('keeps compact density while the viewport crosses the phone boundary', () => {
    const themeMedia = createMediaQuery(false)
    const densityMedia = createMediaQuery(false)
    vi.stubGlobal('matchMedia', vi.fn((query: string) => (
      query === '(max-width: 899px)' ? densityMedia.media : themeMedia.media
    )))
    localStorage.setItem('robopark-theme', 'dark')
    const { result } = renderHook(() => useTheme(), { wrapper })

    act(() => result.current.setDensityPreference('compact'))

    expect(localStorage.getItem('robopark-density')).toBe('compact')
    expect(localStorage.getItem('robopark-theme')).toBe('dark')
    expect(document.documentElement.dataset.density).toBe('compact')

    act(() => densityMedia.dispatch(true))

    expect(document.documentElement.dataset.density).toBe('compact')
    expect(localStorage.getItem('robopark-density')).toBe('compact')
    expect(localStorage.getItem('robopark-theme')).toBe('dark')

    act(() => densityMedia.dispatch(false))

    expect(document.documentElement.dataset.density).toBe('compact')
    expect(localStorage.getItem('robopark-density')).toBe('compact')
    expect(localStorage.getItem('robopark-theme')).toBe('dark')
  })

  it('keeps theme and density selectors usable when storage is denied', () => {
    const themeMedia = createMediaQuery(false)
    const densityMedia = createMediaQuery(false)
    vi.stubGlobal('matchMedia', vi.fn((query: string) => (
      query === '(max-width: 899px)' ? densityMedia.media : themeMedia.media
    )))
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('Storage denied', 'SecurityError')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('Storage denied', 'SecurityError')
    })

    const { result } = renderHook(() => useTheme(), { wrapper })

    expect(result.current.preference).toBe('system')
    expect(result.current.densityPreference).toBe('comfortable')
    expect(result.current.accentPreference).toBe('olive')

    act(() => {
      result.current.setPreference('dark')
      result.current.setDensityPreference('compact')
      result.current.setAccentPreference('warm')
    })

    expect(result.current.preference).toBe('dark')
    expect(result.current.densityPreference).toBe('compact')
    expect(result.current.accentPreference).toBe('warm')
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(document.documentElement.style.colorScheme).toBe('dark')
    expect(document.documentElement.dataset.density).toBe('compact')
    expect(document.documentElement.dataset.accent).toBe('warm')
  })
})
