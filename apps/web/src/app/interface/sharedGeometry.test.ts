/// <reference types="node" />

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, it } from 'vitest'

const legacyCss = readFileSync(resolve('src/index.css'), 'utf8')
const shellCss = readFileSync(resolve('src/app/interface/ShellPrimitives.css'), 'utf8')

it('keeps legacy page, panel, and card geometry on the shared spacing tokens', () => {
  expect(legacyCss).toMatch(/\.page-content\s*\{[^}]*gap:\s*var\(--rp-section-gap\)/s)
  expect(legacyCss).toMatch(/\.page-body\s*\{[^}]*gap:\s*var\(--rp-section-gap\)/s)
  expect(legacyCss).toMatch(/\.panel\s*\{[^}]*gap:\s*var\(--rp-panel-gap\)[^}]*padding:\s*var\(--rp-panel-padding\)[^}]*border-radius:\s*var\(--rp-radius-panel\)[^}]*background:\s*var\(--rp-surface\)/s)
  expect(legacyCss).toMatch(/\.panel\[data-density='dense'\]\s*\{[^}]*gap:\s*var\(--rp-card-gap\)[^}]*padding:\s*var\(--rp-card-padding\)/s)
  expect(legacyCss).toMatch(/\.card-list\s*\{[^}]*gap:\s*var\(--rp-card-gap\)/s)
  expect(legacyCss).toMatch(/\.card\s*\{[^}]*gap:\s*var\(--rp-card-gap\)[^}]*padding:\s*var\(--rp-card-padding\)[^}]*border-radius:\s*var\(--rp-radius-card\)[^}]*background:\s*var\(--rp-surface-sunken\)/s)
})

it('contains and wraps shared legacy content instead of letting text reach panel edges', () => {
  expect(legacyCss).toMatch(/:where\(\.panel, \.card, \.page-header-text, \.panel-head, \.panel-body\)[^{]*\{[^}]*min-inline-size:\s*0[^}]*overflow-wrap:\s*anywhere/s)
  expect(legacyCss).not.toMatch(/\.panel\s*\{[^}]*padding:\s*1rem(?:\s|;)/s)
})

it('contains shell columns and uses the same page gutter on phone screens', () => {
  expect(shellCss).toMatch(/\.rp-shell__main-column\s*\{[^}]*min-inline-size:\s*0/s)
  expect(shellCss).toMatch(/\.rp-shell__content > \*\s*\{[^}]*min-inline-size:\s*0/s)
  expect(shellCss).toMatch(/\.rp-shell__content\s*\{[^}]*padding:\s*var\(--rp-page-gutter\) var\(--rp-shell-gutter\)/s)
})

it('marks the active More menu item without a solid selected block', () => {
  expect(shellCss).toMatch(/\.rp-shell__more-link\.is-active\s*\{[^}]*background:\s*transparent[^}]*border-inline-start-color:\s*var\(--rp-action\)/s)
})
