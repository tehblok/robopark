/// <reference types="node" />

import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

function read(relativePath: string): string {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8')
}

describe('typography bundle', () => {
  it('self-hosts the variable Manrope family under its OFL license', () => {
    const indexCss = read('./index.css')
    const tokensCss = read('./tokens.css')
    const baseCss = read('./base.css')
    const packageJson = JSON.parse(read('../../../package.json'))
    const packageLock = JSON.parse(read('../../../package-lock.json'))
    const installedPackage = packageLock.packages['node_modules/@fontsource-variable/manrope']

    expect(indexCss).toContain("@import '@fontsource-variable/manrope/index.css'")
    expect(tokensCss).toContain("--rp-font-sans: 'Manrope Variable'")
    expect(baseCss).toContain('font-family: var(--rp-font-sans)')
    expect(packageJson.dependencies['@fontsource-variable/manrope']).toBeDefined()
    expect(installedPackage?.license).toBe('OFL-1.1')
    expect(`${indexCss}\n${baseCss}`).not.toMatch(/fonts\.(?:googleapis|gstatic)\.com/i)
  })
})
