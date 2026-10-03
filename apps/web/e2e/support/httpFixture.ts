import { createServer } from 'node:http'
import type { AddressInfo } from 'node:net'

export async function startHttpFixture(handler: (request: Request) => Response | Promise<Response>) {
  const server = createServer(async (incoming, outgoing) => {
    try {
      if (incoming.method === 'OPTIONS') {
        outgoing.writeHead(204, {
          'access-control-allow-headers': incoming.headers['access-control-request-headers'] ?? '*',
          'access-control-allow-methods': 'GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS',
          'access-control-allow-origin': incoming.headers.origin ?? '*',
          'access-control-allow-credentials': 'true',
        }).end()
        return
      }
      const chunks: Buffer[] = []
      for await (const chunk of incoming) chunks.push(Buffer.from(chunk))
      const headers = new Headers()
      for (const [name, value] of Object.entries(incoming.headers)) {
        if (value !== undefined) headers.set(name, Array.isArray(value) ? value.join(', ') : value)
      }
      const body = Buffer.concat(chunks)
      const request = new Request(new URL(incoming.url ?? '/', 'http://127.0.0.1'), {
        method: incoming.method,
        headers,
        body: ['GET', 'HEAD'].includes(incoming.method ?? 'GET') ? undefined : body,
      })
      const response = await handler(request)
      const responseHeaders = Object.fromEntries(response.headers)
      responseHeaders['access-control-allow-origin'] = incoming.headers.origin ?? '*'
      responseHeaders['access-control-allow-credentials'] = 'true'
      outgoing.writeHead(response.status, responseHeaders)
      outgoing.end(Buffer.from(await response.arrayBuffer()))
    } catch (error) {
      console.error(error)
      if (!outgoing.headersSent) outgoing.writeHead(500, { 'access-control-allow-origin': '*', 'content-type': 'application/json' })
      outgoing.end(JSON.stringify({ detail: 'fixture_error' }))
    }
  })
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => { server.off('error', reject); resolve() })
  })
  const port = (server.address() as AddressInfo).port
  return {
    origin: `http://127.0.0.1:${port}`,
    close: () => new Promise<void>((resolve, reject) => {
      server.closeAllConnections()
      server.close(error => { if (error) reject(error); else resolve() })
    }),
  }
}
