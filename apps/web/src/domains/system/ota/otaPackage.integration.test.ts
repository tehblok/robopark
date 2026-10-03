// @vitest-environment node
import { execFileSync } from 'node:child_process'
import { File } from 'node:buffer'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, it } from 'vitest'
import { inspectOtaFile } from './otaManifest'

it('accepts a real OTA from the production builder using the browser inspector', async () => {
  const root = fileURLToPath(new URL('../../../../../../', import.meta.url))
  const scratch = mkdtempSync(join(tmpdir(), 'robopark-browser-ota-'))
  try {
    const artifact = execFileSync('python3', ['-c',
      'from pathlib import Path; import sys; from scripts.build_ota import build_ota; print(build_ota(Path(sys.argv[1]), Path(sys.argv[2]), git_sha="a" * 40))',
      root, scratch,
    ], { cwd: root, encoding: 'utf8', timeout: 15_000 }).trim()
    const inspected = await inspectOtaFile(new File([readFileSync(artifact)], 'release.ota') as unknown as globalThis.File)
    expect(inspected.app_version).toBe(readFileSync(join(root, 'VERSION'), 'utf8').trim())
    expect(inspected.format_version).toBe(1)
    expect(inspected.required_free_bytes).toBeGreaterThan(0)

    // Verify the exact deliverable as well when running release acceptance.
    if (process.env.ROBOPARK_OTA_ACCEPTANCE_FILE) {
      const delivered = await inspectOtaFile(new File([
        readFileSync(process.env.ROBOPARK_OTA_ACCEPTANCE_FILE),
      ], 'delivered.ota') as unknown as globalThis.File)
      expect(delivered.compatible_from.length).toBeGreaterThan(0)
      expect(delivered.format_version).toBe(1)
    }
  } finally {
    rmSync(scratch, { recursive: true, force: true })
  }
}, 20_000)
