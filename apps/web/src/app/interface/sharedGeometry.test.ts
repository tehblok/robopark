/// <reference types="node" />

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, it } from 'vitest'

const legacyCss = readFileSync(resolve('src/index.css'), 'utf8')
const shellCss = readFileSync(resolve('src/app/interface/ShellPrimitives.css'), 'utf8')

it('keeps legacy page, panel, and card geometry on the shared spacing tokens', () => {
  expect(legacyCss).toMatch(/\.page-content\s*\{[^}]*gap:\s*var\(--rp-section-gap\)/s)
  expect(legacyCss).toMatch(/\.page-body\s*\{[^}]*gap:\s*var\(--rp-section-gap\)/s)
  expect(legacyCss).toMatch(/\.panel\s*\{[^}]*gap:\s*var\(--rp-form-gap\)[^}]*padding:\s*var\(--rp-card-padding\)[^}]*border-radius:\s*var\(--rp-radius-card\)[^}]*background:\s*var\(--rp-surface\)/s)
  expect(legacyCss).toMatch(/\.card-list\s*\{[^}]*gap:\s*var\(--rp-form-gap\)/s)
  expect(legacyCss).toMatch(/\.card\s*\{[^}]*padding:\s*var\(--rp-card-padding\)[^}]*border-radius:\s*var\(--rp-radius-card\)[^}]*background:\s*var\(--rp-surface-sunken\)/s)
})

it('contains and wraps shared legacy content instead of letting text reach panel edges', () => {
  expect(legacyCss).toMatch(/:where\(\.panel, \.card, \.page-header-text, \.panel-head, \.panel-body\)[^{]*\{[^}]*min-inline-size:\s*0[^}]*overflow-wrap:\s*anywhere/s)
  expect(legacyCss).not.toMatch(/\.panel\s*\{[^}]*padding:\s*1rem(?:\s|;)/s)
})

it('contains shell columns and uses the same page gutter on phone screens', () => {
  expect(shellCss).toMatch(/\.rp-shell__main-column\s*\{[^}]*min-inline-size:\s*0/s)
  expect(shellCss).toMatch(/\.rp-shell__content > \*\s*\{[^}]*min-inline-size:\s*0/s)
  expect(shellCss).toMatch(/\.rp-shell__content\s*\{[^}]*padding:\s*var\(--rp-section-gap\) var\(--rp-shell-gutter\)/s)
})
