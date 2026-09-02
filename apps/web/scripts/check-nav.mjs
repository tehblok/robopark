#!/usr/bin/env node
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const manifestSource = readFileSync(join(root, 'src/app/routing/routeManifest.ts'), 'utf8')
const routerSource = readFileSync(join(root, 'src/app/routing/AppRouter.tsx'), 'utf8')

const manifestBlock = manifestSource.match(
  /ROUTE_MANIFEST:\s*readonly RouteManifestItem\[\]\s*=\s*\[([\s\S]*?)\]\s*as const/,
)?.[1]
const registryBlock = routerSource.match(
  /ROUTE_ELEMENTS:\s*Record<AppRouteId, ReactElement>\s*=\s*\{([\s\S]*?)\n\}/,
)?.[1]

if (!manifestBlock || !registryBlock) {
  console.error('check-nav: unable to parse route manifest or element registry')
  process.exit(1)
}

const manifestIds = [...manifestBlock.matchAll(/\bid:\s*'([^']+)'/g)].map((match) => match[1])
const registryIds = [...registryBlock.matchAll(/^\s*'([^']+)'\s*:/gm)].map((match) => match[1])

function duplicates(ids) {
  return [...new Set(ids.filter((id, index) => ids.indexOf(id) !== index))]
}

const duplicateManifestIds = duplicates(manifestIds)
const duplicateRegistryIds = duplicates(registryIds)
const missingRegistryIds = manifestIds.filter((id) => !registryIds.includes(id))
const unknownRegistryIds = registryIds.filter((id) => !manifestIds.includes(id))

if (
  duplicateManifestIds.length > 0
  || duplicateRegistryIds.length > 0
  || missingRegistryIds.length > 0
  || unknownRegistryIds.length > 0
) {
  for (const [label, ids] of [
    ['duplicate manifest ids', duplicateManifestIds],
    ['duplicate registry ids', duplicateRegistryIds],
    ['manifest ids missing from registry', missingRegistryIds],
    ['unknown registry ids', unknownRegistryIds],
  ]) {
    if (ids.length > 0) console.error(`check-nav: ${label}: ${ids.join(', ')}`)
  }
  process.exit(1)
}

console.log(`check-nav: ok (${manifestIds.length} route ids)`)
