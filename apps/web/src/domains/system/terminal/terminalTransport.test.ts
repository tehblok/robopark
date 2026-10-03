import { describe, expect, it, vi } from 'vitest'
import { TerminalConnection } from './terminalTransport'
import type { SessionOut } from './terminalApi'

class FakeSocket {
  static instances: FakeSocket[] = []
  readonly sent: Array<string | ArrayBufferLike | Blob | ArrayBufferView> = []
  binaryType = ''
  readyState: number = WebSocket.CONNECTING
  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null

  readonly url: string
  constructor(url: string) { this.url = url; FakeSocket.instances.push(this) }
  send(value: string | ArrayBufferLike | Blob | ArrayBufferView) { this.sent.push(value) }
  close() { this.readyState = WebSocket.CLOSED }
  open() { this.readyState = WebSocket.OPEN; this.onopen?.(new Event('open')) }
  message(data: unknown) { this.onmessage?.(new MessageEvent('message', { data })) }
  disconnect() { this.readyState = WebSocket.CLOSED; this.onclose?.(new CloseEvent('close')) }
}

const session: SessionOut = {
  id: '11111111-1111-4111-8111-111111111111', profile: 'maintenance', state: 'active',
  expires_at: '2026-10-02T12:00:00Z', broker_epoch: 'epoch-a', termination_reason: null,
}

describe('TerminalConnection', () => {
  it('authenticates before becoming ready and sends every input exactly once', async () => {
    FakeSocket.instances = []
    const output = vi.fn()
    const connection = new TerminalConnection({
      socketFactory: (url) => new FakeSocket(url) as unknown as WebSocket,
      ticket: async () => ({ ticket: 'one-use', expires_in: 15 }),
      onOutput: output,
    })
    const connecting = connection.connect(session)
    await vi.waitFor(() => expect(FakeSocket.instances).toHaveLength(1))
    const socket = FakeSocket.instances[0]
    socket.open()
    expect(socket.sent).toEqual([JSON.stringify({ op: 'authenticate', ticket: 'one-use' })])
    socket.message(new Uint8Array([98, 101, 102, 111, 114, 101]).buffer)
    expect(output).not.toHaveBeenCalled()
    expect(() => connection.sendInput(new Uint8Array([120]))).toThrow('terminal_not_connected')
    connection.resize(101, 37)
    expect(socket.sent).toHaveLength(1)
    socket.message(JSON.stringify({ op: 'ready', session }))
    await connecting

    expect(socket.sent[1]).toBe(JSON.stringify({ op: 'resize', cols: 101, rows: 37 }))

    connection.sendInput(new Uint8Array([108, 115, 10]))
    expect(socket.sent).toHaveLength(3)
    expect(Array.from(new Uint8Array(socket.sent[2] as ArrayBuffer))).toEqual([108, 115, 10])
    socket.message(new Uint8Array([111, 107]).buffer)
    expect(output).toHaveBeenCalledWith(new Uint8Array([111, 107]))
  })

  it('reconnects with a fresh ticket and never replays disconnected input', async () => {
    vi.useFakeTimers()
    FakeSocket.instances = []
    const ticket = vi.fn()
      .mockResolvedValueOnce({ ticket: 'first', expires_in: 15 })
      .mockResolvedValueOnce({ ticket: 'second', expires_in: 15 })
    const connection = new TerminalConnection({
      socketFactory: (url) => new FakeSocket(url) as unknown as WebSocket,
      ticket,
      onOutput: vi.fn(),
    })
    const firstConnect = connection.connect(session)
    await Promise.resolve()
    FakeSocket.instances[0].open()
    FakeSocket.instances[0].message(JSON.stringify({ op: 'ready', session }))
    await firstConnect
    connection.sendInput(new Uint8Array([65]))
    FakeSocket.instances[0].disconnect()
    expect(() => connection.sendInput(new Uint8Array([66]))).toThrow('terminal_not_connected')

    await vi.advanceTimersByTimeAsync(250)
    const second = FakeSocket.instances[1]
    second.open()
    second.message(JSON.stringify({ op: 'ready', session: { ...session, state: 'active' } }))
    await vi.runOnlyPendingTimersAsync()
    expect(ticket).toHaveBeenCalledTimes(2)
    expect(second.sent[0]).toBe(JSON.stringify({ op: 'authenticate', ticket: 'second' }))
    expect(second.sent.some(value => typeof value !== 'string')).toBe(false)
    connection.close()
    vi.useRealTimers()
  })

  it('rejects oversized input and clamps resize to the protocol limits', async () => {
    FakeSocket.instances = []
    const connection = new TerminalConnection({
      socketFactory: (url) => new FakeSocket(url) as unknown as WebSocket,
      ticket: async () => ({ ticket: 'ticket', expires_in: 15 }),
      onOutput: vi.fn(),
    })
    const connecting = connection.connect(session)
    await vi.waitFor(() => expect(FakeSocket.instances).toHaveLength(1))
    const socket = FakeSocket.instances[0]
    socket.open(); socket.message(JSON.stringify({ op: 'ready', session })); await connecting
    expect(() => connection.sendInput(new Uint8Array(16 * 1024 + 1))).toThrow('terminal_input_too_large')
    connection.resize(500, 1)
    expect(socket.sent.at(-1)).toBe(JSON.stringify({ op: 'resize', cols: 300, rows: 5 }))
  })

  it('ignores every callback from a stale socket after a newer connect starts', async () => {
    vi.useFakeTimers()
    FakeSocket.instances = []
    const output = vi.fn()
    const state = vi.fn()
    const connection = new TerminalConnection({
      socketFactory: (url) => new FakeSocket(url) as unknown as WebSocket,
      ticket: async () => ({ ticket: '12345678901234567890', expires_in: 15 }),
      onOutput: output, onState: state,
    })
    const firstConnect = connection.connect(session)
    await Promise.resolve()
    const first = FakeSocket.instances[0]
    first.open(); first.message(JSON.stringify({ op: 'ready', session })); await firstConnect

    const secondConnect = connection.connect(session)
    await Promise.resolve()
    const second = FakeSocket.instances[1]
    second.open(); second.message(JSON.stringify({ op: 'ready', session })); await secondConnect
    state.mockClear()
    first.message(new Uint8Array([111, 108, 100]).buffer)
    first.disconnect()
    expect(output).not.toHaveBeenCalled()
    expect(state).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(10_000)
    expect(second.sent).toContain(JSON.stringify({ op: 'heartbeat' }))
    connection.sendInput(new Uint8Array([110, 101, 119]))
    expect(second.sent.some(value => typeof value !== 'string')).toBe(true)
    connection.close()
    vi.useRealTimers()
  })

  it.each([
    { status: 401, detail: 'terminal_login_expired' },
    { status: 403, detail: 'terminal_credentials_changed' },
    { status: 409, detail: 'terminal_capabilities_changed' },
  ])('revokes access when attach-ticket fails with $detail', async failure => {
    vi.useFakeTimers()
    FakeSocket.instances = []
    const revoked = vi.fn()
    const ticket = vi.fn().mockRejectedValue(failure)
    const connection = new TerminalConnection({
      socketFactory: (url) => new FakeSocket(url) as unknown as WebSocket,
      ticket, onOutput: vi.fn(), onAuthorizationFailure: revoked,
    })
    await expect(connection.connect(session)).rejects.toEqual(failure)
    await vi.advanceTimersByTimeAsync(2_000)
    expect(revoked).toHaveBeenCalledOnce()
    expect(ticket).toHaveBeenCalledOnce()
    expect(FakeSocket.instances).toHaveLength(0)
    vi.useRealTimers()
  })

  it('keeps the transport heartbeat alive while the document is hidden', async () => {
    vi.useFakeTimers()
    FakeSocket.instances = []
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
    const connection = new TerminalConnection({
      socketFactory: (url) => new FakeSocket(url) as unknown as WebSocket,
      ticket: async () => ({ ticket: '12345678901234567890', expires_in: 15 }), onOutput: vi.fn(),
    })
    const connecting = connection.connect(session)
    await Promise.resolve()
    const socket = FakeSocket.instances[0]
    socket.open(); socket.message(JSON.stringify({ op: 'ready', session })); await connecting
    await vi.advanceTimersByTimeAsync(10_000)
    expect(socket.sent).toContain(JSON.stringify({ op: 'heartbeat' }))
    connection.close(); hidden.mockRestore(); vi.useRealTimers()
  })

  it('revokes and clears a ready connection on an access-expired server frame', async () => {
    FakeSocket.instances = []
    const revoked = vi.fn()
    const connection = new TerminalConnection({
      socketFactory: (url) => new FakeSocket(url) as unknown as WebSocket,
      ticket: async () => ({ ticket: '12345678901234567890', expires_in: 15 }),
      onOutput: vi.fn(), onAuthorizationFailure: revoked,
    })
    const connecting = connection.connect(session)
    await Promise.resolve()
    const socket = FakeSocket.instances[0]
    socket.open(); socket.message(JSON.stringify({ op: 'ready', session })); await connecting
    socket.message(JSON.stringify({ op: 'error', error: 'terminal_access_expired' }))
    expect(revoked).toHaveBeenCalledOnce()
    expect(() => connection.sendInput(new Uint8Array([120]))).toThrow('terminal_not_connected')
  })
})
