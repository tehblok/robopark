#!/usr/bin/env node
/**
 * Ensures every nav item path is registered in App.tsx.
 * Run from apps/web: node scripts/check-nav.mjs
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const navSource = readFileSync(join(root, 'src/nav.ts'), 'utf8')
const appSource = readFileSync(join(root, 'src/App.tsx'), 'utf8')

const navPaths = [...navSource.matchAll(/path:\s*'([^']+)'/g)].map((match) => match[1])
const routePaths = new Set(
  [...appSource.matchAll(/path="([^"]+)"/g)].map((match) => match[1]),
)

const missing = navPaths.filter((path) => !routePaths.has(path))

if (missing.length > 0) {
  console.error('check-nav: nav paths missing from App.tsx routes:')
  for (const path of missing) {
    console.error(`  - ${path}`)
  }
  process.exit(1)
}

console.log(`check-nav: ok (${navPaths.length} nav paths)`)
