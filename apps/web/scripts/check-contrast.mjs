import { readFileSync } from 'node:fs'

const tokensPath = process.argv[2] ?? new URL('../src/design-system/styles/tokens.css', import.meta.url)
const css = readFileSync(tokensPath, 'utf8')

const commonTokens = [
  '--rp-font-sans',
  '--rp-font-mono',
  '--rp-font-size-caption',
  '--rp-font-size-body',
  '--rp-font-size-control',
  '--rp-font-size-title',
  '--rp-font-size-display',
  '--rp-line-height-compact',
  '--rp-line-height-body',
  '--rp-font-weight-regular',
  '--rp-font-weight-medium',
  '--rp-font-weight-bold',
  '--rp-space-1',
  '--rp-space-2',
  '--rp-space-3',
  '--rp-space-4',
  '--rp-space-6',
  '--rp-space-8',
  '--rp-control-min-size',
  '--rp-radius-control',
  '--rp-radius-panel',
  '--rp-motion-fast',
  '--rp-motion-normal',
]

const themeTokens = [
  '--rp-canvas',
  '--rp-surface',
  '--rp-surface-elevated',
  '--rp-surface-sunken',
  '--rp-text',
  '--rp-text-muted',
  '--rp-text-inverse',
  '--rp-text-disabled',
  '--rp-border',
  '--rp-divider',
  '--rp-action',
  '--rp-action-on',
  '--rp-action-hover',
  '--rp-critical',
  '--rp-critical-on',
  '--rp-critical-surface',
  '--rp-warning',
  '--rp-warning-surface',
  '--rp-success',
  '--rp-success-surface',
  '--rp-info',
  '--rp-info-surface',
  '--rp-focus',
  '--rp-state-hover',
  '--rp-state-selected',
  '--rp-state-disabled',
  '--rp-state-pressed',
  '--rp-chart-1',
  '--rp-chart-2',
  '--rp-chart-3',
  '--rp-chart-4',
  '--rp-overlay',
  '--rp-shadow-panel',
]

const densityTokens = [
  '--rp-row-min-size',
  '--rp-density-gap',
  '--rp-density-panel-padding',
]

const contrastPairs = [
  ['text', 'surface'],
  ['text', 'canvas'],
  ['text-muted', 'surface'],
  ['action-on', 'action'],
  ['action-on', 'action-hover'],
  ['critical-on', 'critical'],
  ['warning', 'warning-surface'],
  ['success', 'success-surface'],
  ['info', 'info-surface'],
]

function declarations(block) {
  return new Map(
    [...block.matchAll(/(--rp-[\w-]+)\s*:\s*([^;]+);/g)]
      .map(([, name, value]) => [name, value.trim()]),
  )
}

function blockFor(pattern, label) {
  const match = css.match(pattern)
  if (!match) throw new Error(`Missing ${label} token block`)
  return declarations(match[1])
}

function hexToRgb(value) {
  const match = value.match(/^#([\da-f]{6})$/i)
  if (!match) throw new Error(`Expected a six-digit hex color, received ${value}`)
  const number = Number.parseInt(match[1], 16)
  return [(number >> 16) & 255, (number >> 8) & 255, number & 255]
}

function luminance(value) {
  const channels = hexToRgb(value).map((channel) => {
    const normalized = channel / 255
    return normalized <= 0.04045
      ? normalized / 12.92
      : ((normalized + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]
}

function contrast(foreground, background) {
  const first = luminance(foreground)
  const second = luminance(background)
  const lighter = Math.max(first, second)
  const darker = Math.min(first, second)
  return (lighter + 0.05) / (darker + 0.05)
}

const common = blockFor(/:root\s*\{([\s\S]*?)\}/, 'common')
const failures = []

function requireTokens(tokens, requiredTokens, label) {
  for (const token of requiredTokens) {
    if (!tokens.has(token)) failures.push(`${label}: missing ${token}`)
  }
}

for (const density of ['comfortable', 'compact']) {
  const tokens = blockFor(
    new RegExp(`:root\\[data-density=['"]${density}['"]\\]\\s*\\{([\\s\\S]*?)\\}`),
    `${density} density`,
  )
  requireTokens(tokens, densityTokens, density)
}

for (const theme of ['light', 'dark']) {
  const themed = blockFor(
    new RegExp(`:root\\[data-theme=['"]${theme}['"]\\]\\s*\\{([\\s\\S]*?)\\}`),
    theme,
  )
  const tokens = new Map([...common, ...themed])

  requireTokens(tokens, [...commonTokens, ...themeTokens], theme)

  for (const [foregroundName, backgroundName] of contrastPairs) {
    const foreground = tokens.get(`--rp-${foregroundName}`)
    const background = tokens.get(`--rp-${backgroundName}`)
    if (!foreground || !background) continue
    const ratio = contrast(foreground, background)
    const result = `${theme} ${foregroundName}/${backgroundName}: ${ratio.toFixed(2)}`
    console.log(result)
    if (ratio < 4.5) failures.push(`${result} is below 4.5`)
  }
}

if (failures.length > 0) {
  failures.forEach((failure) => console.error(failure))
  process.exitCode = 1
}
