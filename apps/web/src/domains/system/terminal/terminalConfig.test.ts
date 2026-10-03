// @vitest-environment node
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, it } from 'vitest'

it('places a bounded no-store websocket proxy before the general API location', () => {
  const nginx = readFileSync(resolve('nginx.conf'), 'utf8')
  const websocket = nginx.indexOf('location ~ "^/api/admin/terminal/sessions/')
  const genericApi = nginx.indexOf('location /api/ {')
  expect(websocket).toBeGreaterThan(0)
  expect(websocket).toBeLessThan(genericApi)
  const block = nginx.slice(websocket, nginx.indexOf('\n    }', websocket))
  expect(block).toContain('proxy_set_header Upgrade $http_upgrade;')
  expect(block).toContain('proxy_set_header Connection "upgrade";')
  expect(block).toContain('proxy_http_version 1.1;')
  expect(block).toContain('proxy_buffering off;')
  expect(block).toContain('proxy_request_buffering off;')
  expect(block).toContain('proxy_read_timeout 45s;')
  expect(block).toContain('Cache-Control "no-store"')
})

it('serves terminal HTML exactly with no offline fallback and no clipboard permission', () => {
  const nginx = readFileSync(resolve('nginx.conf'), 'utf8')
  const start = nginx.indexOf('location = /terminal.html')
  const block = nginx.slice(start, nginx.indexOf('\n    }', start))
  expect(start).toBeGreaterThan(0)
  expect(block).toContain('try_files $uri =404;')
  expect(block).toContain('no-store')
  expect(block).toContain('clipboard-read=(), clipboard-write=()')
})

it('ships only xterm core and the compatible fit addon', () => {
  const manifest = JSON.parse(readFileSync(resolve('package.json'), 'utf8')) as { dependencies: Record<string, string> }
  expect(manifest.dependencies['@xterm/xterm']).toBe('6.0.0')
  expect(manifest.dependencies['@xterm/addon-fit']).toBe('0.11.0')
  expect(Object.keys(manifest.dependencies).filter(name => name.startsWith('@xterm/')).sort()).toEqual(['@xterm/addon-fit', '@xterm/xterm'])
})
