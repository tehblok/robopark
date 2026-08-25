import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const src = readFileSync(join(root, 'src/nav.ts'), 'utf8')
const start = src.indexOf('PRIMARY_NAV_IDS')
if (start < 0) {
  console.error('PRIMARY_NAV_IDS missing')
  process.exit(1)
}
const block = src.slice(start, start + 280)
for (const id of ['dashboard', 'tasks', 'emergency', 'reports']) {
  if (!block.includes(`'${id}'`)) {
    console.error('PRIMARY_NAV_IDS missing', id)
    process.exit(1)
  }
}
if (!src.includes('function primaryNavItems') || !src.includes('function moreNavItems')) {
  console.error('missing primaryNavItems/moreNavItems')
  process.exit(1)
}
console.log('nav check ok')
