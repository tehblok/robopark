import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { readProductionCsp } from './pwa-fixture-csp.mjs'

const nginx = readFileSync(new URL('../nginx.conf', import.meta.url), 'utf8')

test('PWA uses the application document CSP while the terminal keeps its stricter policy', () => {
  const application = readProductionCsp(nginx, '/index.html')
  const terminal = readProductionCsp(nginx, '/terminal.html')
  assert.match(application, /img-src 'self' data: blob:/)
  assert.match(application, /base-uri 'self'/)
  assert.match(terminal, /img-src 'self' data:;/)
  assert.match(terminal, /base-uri 'none'/)
  assert.doesNotMatch(terminal, /blob:/)
  assert.notEqual(application, terminal)
  for (const policy of [application, terminal]) {
    assert.match(policy, /script-src 'self';/)
    assert.match(policy, /object-src 'none';/)
  }
})

test('missing or ambiguous document locations and headers fail closed', () => {
  const location = `location = /index.html {
    add_header Content-Security-Policy "default-src 'self'" always;
  }`
  assert.throws(() => readProductionCsp('', '/index.html'))
  assert.throws(() => readProductionCsp(location, '/terminal.html'))
  assert.throws(() => readProductionCsp(location + '\n' + location, '/index.html'))
  assert.throws(() => readProductionCsp('location = /index.html {\n}', '/index.html'))
  const duplicateHeader = location.replace('\n  }', `
    add_header Content-Security-Policy "default-src 'none'" always;
  }`)
  assert.throws(() => readProductionCsp(duplicateHeader, '/index.html'))
})
