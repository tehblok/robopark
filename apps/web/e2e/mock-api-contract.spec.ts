import { expect, test } from '@playwright/test'
import { installMockApi, type MockRoute } from './support/mockApi'

test('custom handlers receive standards-based DOM requests with query, JSON, and multipart bytes', async ({ page }) => {
  const routes: MockRoute[] = [
    {
      method: 'GET',
      path: '/api/contract/query',
      handler: (request) => ({
        json: {
          method: request.method,
          query: new URL(request.url).searchParams.get('park'),
        },
      }),
    },
    {
      method: 'POST',
      path: '/api/contract/json',
      handler: async (request) => ({ json: await request.json() }),
    },
    {
      method: 'POST',
      path: /^\/api\/contract\/multipart$/,
      handler: async (request) => {
        const form = await request.formData()
        const file = form.get('file')
        if (!(file instanceof File)) throw new Error('Expected a DOM File')
        return {
          json: {
            kind: form.get('kind'),
            file: { name: file.name, type: file.type, size: file.size },
          },
        }
      },
    },
  ]

  await installMockApi(page, { routes })
  await page.goto('/login')

  const observed = await page.evaluate(async () => {
    const form = new FormData()
    form.set('kind', 'device_photo')
    form.set('file', new File(['robot'], 'robot.txt', { type: 'text/plain' }))

    const [queryResponse, jsonResponse, multipartResponse] = await Promise.all([
      fetch('/api/contract/query?park=7'),
      fetch('/api/contract/json', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ issue: 'ROBOPARK-42' }),
      }),
      fetch('/api/contract/multipart', { method: 'POST', body: form }),
    ])

    return {
      query: await queryResponse.json(),
      json: await jsonResponse.json(),
      multipart: await multipartResponse.json(),
    }
  })

  expect(observed.query).toEqual({ method: 'GET', query: '7' })
  expect(observed.json).toEqual({ issue: 'ROBOPARK-42' })
  expect(observed.multipart).toEqual({
    kind: 'device_photo',
    file: { name: 'robot.txt', type: 'text/plain', size: 5 },
  })
})

test('custom routes precede pathname-only defaults and missing fixtures fail loudly', async ({ page }) => {
  await installMockApi(page, {
    routes: [{
      method: 'GET',
      path: '/api/parks',
      handler: () => ({ status: 202, json: { source: 'custom' } }),
    }],
  })
  await page.goto('/login')

  const observed = await page.evaluate(async () => {
    const custom = await fetch('/api/parks?park=7')
    const missing = await fetch('/api/not-configured?park=7')
    return {
      custom: { status: custom.status, body: await custom.json() },
      missing: { status: missing.status, body: await missing.json() },
    }
  })

  expect(observed).toEqual({
    custom: { status: 202, body: { source: 'custom' } },
    missing: { status: 404, body: { detail: 'mock_not_configured' } },
  })
})

test('dashboard defaults keep the Task 8 overview in a deterministic ready state', async ({ page }) => {
  await installMockApi(page)
  await page.goto('/login')

  const observed = await page.evaluate(async () => {
    const summary = await fetch('/api/dashboard/summary?park_id=7')
    const history = await fetch('/api/dashboard/history?park_id=7&days=7')
    return Promise.all([summary.json(), history.json()])
  })

  expect(observed).toEqual([
    { park_id: 7, arrived: 0, done: 0, queued: 0, in_transit: 0, moving: [] },
    { park_id: 7, points: [] },
  ])
})
