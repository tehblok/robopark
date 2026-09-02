/// <reference types="node" />

import { spawnSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
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

  it.each([
    {
      label: 'comfortable',
      blockStart: ":root[data-density='comfortable'] {",
      token: '--rp-control-min-size',
      expected: 'comfortable: missing --rp-control-min-size',
    },
    {
      label: 'compact',
      blockStart: ":root[data-density='compact'] {",
      token: '--rp-row-min-size',
      expected: 'compact: missing --rp-row-min-size',
    },
    {
      label: 'narrow override',
      blockStart: '@media (max-width: 899px) {',
      token: '--rp-density-gap',
      expected: 'narrow: missing --rp-density-gap',
    },
  ])('rejects a missing density token in the $label block', ({ blockStart, token, expected }) => {
    const tokensCss = read('./tokens.css')
    const blockStartIndex = tokensCss.indexOf(blockStart)
    const blockEndIndex = tokensCss.indexOf('}', blockStartIndex) + 1
    const block = tokensCss.slice(blockStartIndex, blockEndIndex)
    const brokenBlock = block.replace(new RegExp(`\\n\\s+${token}: [^;]+;`), '')
    const brokenTokensCss = tokensCss.replace(block, brokenBlock)
    expect(brokenTokensCss).not.toBe(tokensCss)
    const fixtureDirectory = mkdtempSync(join(tmpdir(), 'robopark-contrast-'))
    const fixturePath = join(fixtureDirectory, 'tokens.css')
    writeFileSync(fixturePath, brokenTokensCss)

    try {
      const relativeScriptPath = '../../../scripts/check-contrast.mjs'
      const scriptPath = fileURLToPath(new URL(relativeScriptPath, import.meta.url))
      const result = spawnSync(process.execPath, [scriptPath, fixturePath], { encoding: 'utf8' })

      expect(result.status).toBe(1)
      expect(result.stderr).toContain(expected)
    } finally {
      rmSync(fixtureDirectory, { recursive: true })
    }
  })
})
