/// <reference types="node" />

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, it } from 'vitest'

const css = readFileSync(resolve('src/design-system/styles/tokens.css'), 'utf8')

function color(theme: 'light' | 'dark', token: string): [number, number, number] {
  const block = css.match(new RegExp(`:root\\[data-theme='${theme}'\\]\\s*\\{([^}]+)\\}`))?.[1]
  const hex = block?.match(new RegExp(`--rp-${token}:\\s*#([0-9a-f]{6});`, 'i'))?.[1]
  if (!hex) throw new Error(`Missing ${theme} ${token}`)
  return [0, 2, 4].map(index => Number.parseInt(hex.slice(index, index + 2), 16)) as [number, number, number]
}

function luminance([red, green, blue]: [number, number, number]): number {
  const channels = [red, green, blue].map(channel => {
    const value = channel / 255
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
  })
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722
}

function ratio(first: [number, number, number], second: [number, number, number]): number {
  const values = [luminance(first), luminance(second)].sort((a, b) => b - a)
  return (values[0] + 0.05) / (values[1] + 0.05)
}

function accentColor(
  theme: 'light' | 'dark',
  accent: 'blue' | 'violet' | 'warm',
  token: 'action' | 'action-on' | 'focus' | 'state-selected',
): [number, number, number] {
  const block = css.match(new RegExp(`:root\\[data-theme='${theme}'\\]\\[data-accent='${accent}'\\]\\s*\\{([^}]+)\\}`))?.[1]
  const hex = block?.match(new RegExp(`--rp-${token}:\\s*#([0-9a-f]{6});`, 'i'))?.[1]
  if (!hex) throw new Error(`Missing ${theme} ${accent} ${token}`)
  return [0, 2, 4].map(index => Number.parseInt(hex.slice(index, index + 2), 16)) as [number, number, number]
}

it('keeps the system accent olive and dark surfaces neutral in both themes', () => {
  for (const theme of ['light', 'dark'] as const) {
    for (const token of ['action', 'state-selected']) {
      const [red, green, blue] = color(theme, token)
      expect(green, `${theme} ${token} green`).toBeGreaterThan(red)
      expect(red, `${theme} ${token} red`).toBeGreaterThan(blue)
    }
  }
  for (const token of ['canvas', 'surface', 'surface-elevated', 'surface-sunken']) {
    const [red, green, blue] = color('dark', token)
    expect(green, `dark ${token} green`).toBeGreaterThanOrEqual(blue)
    expect(blue - red, `dark ${token} blue cast`).toBeLessThanOrEqual(2)
  }
})

it('uses the theme foreground on the selected authentication checkbox', () => {
  const authCss = readFileSync(resolve('src/components/auth/auth.css'), 'utf8')
  expect(authCss).toMatch(/\.rp-auth__card \.field-check input:checked::after\s*\{[^}]*background:\s*var\(--rp-action-on\)/s)
})

it('keeps every optional accent readable in light and dark themes', () => {
  for (const theme of ['light', 'dark'] as const) {
    for (const accent of ['blue', 'violet', 'warm'] as const) {
      expect(ratio(accentColor(theme, accent, 'action'), accentColor(theme, accent, 'action-on')))
        .toBeGreaterThanOrEqual(4.5)
      expect(ratio(accentColor(theme, accent, 'focus'), color(theme, 'surface')))
        .toBeGreaterThanOrEqual(3)
    }
  }
})

it('keeps canvas and panel surfaces outside accent overrides', () => {
  for (const theme of ['light', 'dark'] as const) {
    for (const accent of ['blue', 'violet', 'warm'] as const) {
      const block = css.match(new RegExp(`:root\\[data-theme='${theme}'\\]\\[data-accent='${accent}'\\]\\s*\\{([^}]+)\\}`))?.[1]
      expect(block).toBeTruthy()
      expect(block).not.toMatch(/--rp-(canvas|surface|surface-elevated|surface-sunken):/)
    }
  }
})

it('keeps shared surfaces, text and borders visually neutral', () => {
  for (const theme of ['light', 'dark'] as const) {
    for (const token of ['canvas', 'surface', 'surface-elevated', 'surface-sunken', 'text', 'text-muted', 'border', 'divider']) {
      const channels = color(theme, token)
      expect(Math.max(...channels) - Math.min(...channels), `${theme} ${token}`).toBeLessThanOrEqual(8)
    }
  }
})
