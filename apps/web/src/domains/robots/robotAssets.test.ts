import { readdirSync, statSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

describe('robot illustrations transfer budget', () => {
  it('keeps all diagnostic views below 750 KiB for weak uplinks and OTA', () => {
    const directory = resolve(process.cwd(), 'src/assets/robots')
    const bytes = readdirSync(directory)
      .filter(name => /\.(png|webp)$/i.test(name))
      .reduce((sum, name) => sum + statSync(resolve(directory, name)).size, 0)

    expect(bytes).toBeLessThan(750 * 1024)
  })
})
