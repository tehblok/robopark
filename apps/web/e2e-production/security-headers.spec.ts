import { expect, test } from '@playwright/test'

type SecurityProbeResult = {
  blobImageDecoded: boolean
  externalLoaded: boolean
  foreignRan: boolean
  inlineRan: boolean
  violations: { blockedURI: string; effectiveDirective: string }[]
}

test('production CSP permits local photo previews without weakening script or object isolation', async ({ page }) => {
  const response = await page.goto('/__pwa_fixture__/security-probe.html')
  expect(response).not.toBeNull()
  const policy = response?.headers()['content-security-policy'] ?? ''

  await page.waitForFunction(() => 'securityProbeResult' in globalThis)
  const result = await page.evaluate(async () => await (globalThis as typeof globalThis & {
    securityProbeResult: Promise<SecurityProbeResult>
  }).securityProbeResult)

  expect(result.externalLoaded).toBe(true)
  expect(result.blobImageDecoded, JSON.stringify(result, null, 2)).toBe(true)
  expect(result.inlineRan).toBe(false)
  expect(result.foreignRan).toBe(false)
  expect(result.violations.some(item => item.effectiveDirective.startsWith('script-src') && item.blockedURI === 'inline')).toBe(true)
  expect(result.violations.some(item => item.effectiveDirective.startsWith('script-src') && item.blockedURI.includes('__csp_foreign_script__.js'))).toBe(true)
  expect(result.violations.some(item => item.effectiveDirective === 'object-src')).toBe(true)

  expect(policy).toContain("script-src 'self'")
  expect(policy).not.toContain("script-src 'self' 'unsafe-inline'")
  expect(policy).toContain("img-src 'self' data: blob:")
  expect(policy).toContain("object-src 'none'")
  expect(policy).toContain("frame-ancestors 'none'")
})
