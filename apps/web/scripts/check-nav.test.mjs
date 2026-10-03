import assert from 'node:assert/strict'
import { test } from 'node:test'
import { checkNavigationSources } from './check-nav.mjs'

test('structural parser accepts quoted and unquoted keys, comments and const assertions', () => {
  const manifest = `export const ROUTE_MANIFEST = ([
    { id: 'overview', nav: { mobilePriority: { driver: 1 } } },
    /* id: 'fake' */ { 'id': 'robot-check' },
  ] as const) satisfies readonly unknown[]`
  const router = `export const ROUTE_ELEMENTS = ({
    overview: <OverviewPage />, // 'fake': <Fake />,
    'robot-check': <RobotCheckPage />,
  } as const) satisfies Record<string, unknown>`
  assert.deepEqual(checkNavigationSources(manifest, router), { count: 2, errors: [] })
})

test('reports duplicate manifest and registry ids and missing or extra registrations', () => {
  const result = checkNavigationSources(
    `const ROUTE_MANIFEST = [{ id: 'work' }, { "id": 'work' }, { id: 'robots' }]`,
    `const ROUTE_ELEMENTS = { work: <Work />, 'work': <Work />, surplus: <Other /> }`,
  )
  assert.deepEqual(result.errors, [
    'duplicate manifest ids: work',
    'duplicate registry ids: work',
    'manifest ids missing from registry: robots',
    'unknown registry ids: surplus',
  ])
})

test('rejects absent or dynamic registrations instead of silently certifying a partial registry', () => {
  for (const [manifest, router] of [
    ['', ''],
    [`const ROUTE_MANIFEST = [{ id: dynamic }]`, `const ROUTE_ELEMENTS = {}`],
    [`const ROUTE_MANIFEST = []`, `const ROUTE_ELEMENTS = { ...other }`],
    [`const ROUTE_MANIFEST = [...other]`, `const ROUTE_ELEMENTS = {}`],
    [`const ROUTE_MANIFEST = []`, `const ROUTE_ELEMENTS = { [dynamic]: <Page /> }`],
  ]) assert.throws(() => checkNavigationSources(manifest, router), /unable to parse/)
})
