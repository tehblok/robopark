#!/usr/bin/env node
import { readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import ts from 'typescript'

function parseError(message) {
  throw new Error(`unable to parse route manifest or element registry: ${message}`)
}

function unwrap(node) {
  while (node && (ts.isParenthesizedExpression(node) || ts.isAsExpression(node) || ts.isSatisfiesExpression(node))) {
    node = node.expression
  }
  return node
}

function initializer(source, name, kind) {
  const file = ts.createSourceFile('routes.tsx', source, ts.ScriptTarget.Latest, true, kind)
  if (file.parseDiagnostics.length) parseError(`${name} has syntax errors`)
  const declarations = file.statements.filter(ts.isVariableStatement)
    .flatMap(statement => [...statement.declarationList.declarations])
    .filter(declaration => ts.isIdentifier(declaration.name) && declaration.name.text === name)
  if (declarations.length !== 1) parseError(`${name} must be declared once`)
  const node = unwrap(declarations[0].initializer)
  if (!node) parseError(`${name} has no initializer`)
  return node
}

function propertyName(property) {
  if (!ts.isPropertyAssignment(property)) parseError('expected an explicit property assignment')
  const name = property.name
  if (!ts.isIdentifier(name) && !ts.isStringLiteral(name)) parseError('expected a static property name')
  return name.text
}

export function parseManifestIds(source) {
  const array = initializer(source, 'ROUTE_MANIFEST', ts.ScriptKind.TS)
  if (!ts.isArrayLiteralExpression(array)) parseError('ROUTE_MANIFEST must be an array')
  return array.elements.map(element => {
    const record = unwrap(element)
    if (!ts.isObjectLiteralExpression(record)) parseError('manifest routes must be explicit objects')
    const ids = record.properties.filter(property => propertyName(property) === 'id')
    if (ids.length !== 1) parseError('each route must declare one id')
    const id = unwrap(ids[0].initializer)
    if (!ts.isStringLiteral(id)) parseError('route id must be a string literal')
    return id.text
  })
}

export function parseRegistryIds(source) {
  const object = initializer(source, 'ROUTE_ELEMENTS', ts.ScriptKind.TSX)
  if (!ts.isObjectLiteralExpression(object)) parseError('ROUTE_ELEMENTS must be an object')
  return object.properties.map(propertyName)
}

function duplicates(ids) {
  return [...new Set(ids.filter((id, index) => ids.indexOf(id) !== index))]
}

export function checkNavigationSources(manifestSource, routerSource) {
  const manifestIds = parseManifestIds(manifestSource)
  const registryIds = parseRegistryIds(routerSource)
  const checks = [
    ['duplicate manifest ids', duplicates(manifestIds)],
    ['duplicate registry ids', duplicates(registryIds)],
    ['manifest ids missing from registry', manifestIds.filter(id => !registryIds.includes(id))],
    ['unknown registry ids', registryIds.filter(id => !manifestIds.includes(id))],
  ]
  return {
    count: manifestIds.length,
    errors: checks.filter(([, ids]) => ids.length).map(([label, ids]) => `${label}: ${ids.join(', ')}`),
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    const root = join(dirname(fileURLToPath(import.meta.url)), '..')
    const result = checkNavigationSources(
      readFileSync(join(root, 'src/app/routing/routeManifest.ts'), 'utf8'),
      readFileSync(join(root, 'src/app/routing/AppRouter.tsx'), 'utf8'),
    )
    if (result.errors.length) {
      result.errors.forEach(error => console.error(`check-nav: ${error}`))
      process.exitCode = 1
    } else console.log(`check-nav: ok (${result.count} route ids)`)
  } catch (error) {
    console.error(`check-nav: ${error.message}`)
    process.exitCode = 1
  }
}
