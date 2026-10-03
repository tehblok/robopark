import type { SessionOut } from './terminalApi'

const MAX_INPUT_BYTES = 16 * 1024
const RECONNECT_WINDOW_MS = 15_000
const ACCESS_REVOCATION_ERRORS = new Set([
  'terminal_login_expired', 'terminal_forbidden', 'terminal_access_revoked',
  'terminal_access_expired', 'terminal_credentials_changed', 'terminal_capabilities_changed',
])

type ConnectionOptions = {
  ticket: (sessionId: string) => Promise<{ ticket: string; expires_in: number }>
  socketFactory?: (url: string) => WebSocket
  onOutput: (bytes: Uint8Array) => void
  onState?: (state: 'connecting' | 'active' | 'detached' | 'ended', reason?: string) => void
  onSession?: (session: SessionOut) => void
  onAuthorizationFailure?: () => void
}

export class TerminalConnection {
  private readonly options: ConnectionOptions
  private socket: WebSocket | null = null
  private readySocket: WebSocket | null = null
  private session: SessionOut | null = null
  private explicitlyClosed = false
  private firstDetachedAt = 0
  private reconnectTimer: number | null = null
  private heartbeatTimer: number | null = null
  private heartbeatSocket: WebSocket | null = null
  private connectGeneration = 0
  private pendingResize: { cols: number; rows: number } | null = null
  private outputHandler: (bytes: Uint8Array) => void

  constructor(options: ConnectionOptions) {
    this.options = options
    this.outputHandler = options.onOutput
  }

  setOutputHandler(handler: (bytes: Uint8Array) => void): () => void {
    this.outputHandler = handler
    return () => { if (this.outputHandler === handler) this.outputHandler = () => {} }
  }

  async connect(session: SessionOut): Promise<void> {
    const previous = this.socket
    const generation = ++this.connectGeneration
    this.socket = null
    this.readySocket = null
    this.stopHeartbeat(previous)
    previous?.close()
    this.session = session
    this.explicitlyClosed = false
    this.firstDetachedAt = 0
    return this.open(generation)
  }

  private async open(generation: number): Promise<void> {
    const session = this.session
    if (!session || this.explicitlyClosed || generation !== this.connectGeneration) return
    this.options.onState?.('connecting')
    let ticket: string
    try {
      ({ ticket } = await this.options.ticket(session.id))
    } catch (error) {
      if (generation === this.connectGeneration && this.isAccessRevocation(error)) this.revokeAccess()
      throw error
    }
    if (this.explicitlyClosed || generation !== this.connectGeneration) return
    const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${scheme}//${window.location.host}/api/admin/terminal/sessions/${encodeURIComponent(session.id)}/stream`
    const socket = (this.options.socketFactory ?? ((value) => new WebSocket(value)))(url)
    socket.binaryType = 'arraybuffer'
    this.socket = socket
    await new Promise<void>((resolve, reject) => {
      let ready = false
      socket.onopen = () => {
        if (!this.isCurrent(socket, generation)) return
        socket.send(JSON.stringify({ op: 'authenticate', ticket }))
        ticket = ''
      }
      socket.onerror = () => {
        if (!this.isCurrent(socket, generation)) return
        if (!ready) reject(new Error('terminal_connection_failed'))
      }
      socket.onmessage = event => {
        if (!this.isCurrent(socket, generation)) return
        if (event.data instanceof ArrayBuffer) {
          if (ready && this.readySocket === socket) this.outputHandler(new Uint8Array(event.data))
          return
        }
        if (ArrayBuffer.isView(event.data)) {
          if (ready && this.readySocket === socket) this.outputHandler(new Uint8Array(event.data.buffer, event.data.byteOffset, event.data.byteLength))
          return
        }
        if (typeof event.data !== 'string') return
        let message: unknown
        try { message = JSON.parse(event.data) } catch { return }
        if (!message || typeof message !== 'object') return
        const value = message as { op?: string; session?: SessionOut; reason?: string; error?: string }
        if (value.op === 'ready' && value.session) {
          ready = true
          this.readySocket = socket
          this.firstDetachedAt = 0
          this.session = value.session
          this.options.onSession?.(value.session)
          this.options.onState?.('active')
          this.startHeartbeat(socket, generation)
          if (this.pendingResize) socket.send(JSON.stringify({ op: 'resize', ...this.pendingResize }))
          resolve()
        } else if (value.op === 'ended') {
          this.options.onState?.('ended', value.reason)
          this.close()
        } else if (value.op === 'error') {
          if (value.error && ACCESS_REVOCATION_ERRORS.has(value.error)) this.revokeAccess()
          this.options.onState?.('ended', value.error)
          this.close()
          if (!ready) reject(new Error(value.error ?? 'terminal_error'))
        }
      }
      socket.onclose = () => {
        if (!this.isCurrent(socket, generation)) return
        const wasReady = ready
        ready = false
        this.readySocket = null
        this.stopHeartbeat(socket)
        this.socket = null
        if (!wasReady) reject(new Error('terminal_connection_closed'))
        else if (!this.explicitlyClosed && this.session?.state !== 'ended') this.scheduleReconnect()
      }
    })
  }

  sendInput(bytes: Uint8Array): void {
    if (bytes.byteLength > MAX_INPUT_BYTES) throw new Error('terminal_input_too_large')
    if (!bytes.byteLength) return
    if (!this.socket || this.readySocket !== this.socket || this.socket.readyState !== WebSocket.OPEN) throw new Error('terminal_not_connected')
    this.socket.send(bytes.slice().buffer as ArrayBuffer)
  }

  resize(cols: number, rows: number): void {
    this.pendingResize = { cols: Math.max(20, Math.min(300, Math.round(cols))), rows: Math.max(5, Math.min(150, Math.round(rows))) }
    if (!this.socket || this.readySocket !== this.socket || this.socket.readyState !== WebSocket.OPEN) return
    this.socket.send(JSON.stringify({ op: 'resize', ...this.pendingResize }))
  }

  close(): void {
    this.explicitlyClosed = true
    this.connectGeneration++
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer)
    this.reconnectTimer = null
    const socket = this.socket
    this.socket = null
    this.readySocket = null
    this.stopHeartbeat(socket)
    socket?.close()
  }

  private scheduleReconnect() {
    if (this.explicitlyClosed || !this.session || this.session.state === 'ended') return
    const now = Date.now()
    if (!this.firstDetachedAt) this.firstDetachedAt = now
    if (now - this.firstDetachedAt >= RECONNECT_WINDOW_MS) {
      this.options.onState?.('ended', 'reconnect_timeout')
      return
    }
    this.options.onState?.('detached')
    const generation = ++this.connectGeneration
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null
      void this.open(generation).catch(() => this.scheduleReconnect())
    }, 250)
  }

  private startHeartbeat(socket: WebSocket, generation: number) {
    this.stopHeartbeat(this.heartbeatSocket)
    this.heartbeatSocket = socket
    this.heartbeatTimer = window.setInterval(() => {
      if (this.isCurrent(socket, generation) && this.readySocket === socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ op: 'heartbeat' }))
      }
    }, 10_000)
  }

  private stopHeartbeat(socket: WebSocket | null) {
    if (socket !== this.heartbeatSocket) return
    if (this.heartbeatTimer !== null) window.clearInterval(this.heartbeatTimer)
    this.heartbeatTimer = null
    this.heartbeatSocket = null
  }

  private isCurrent(socket: WebSocket, generation: number): boolean {
    return !this.explicitlyClosed && this.socket === socket && this.connectGeneration === generation
  }

  private isAccessRevocation(error: unknown): boolean {
    if (!error || typeof error !== 'object') return false
    const value = error as { status?: unknown; detail?: unknown }
    return value.status === 401 || value.status === 403
      || typeof value.detail === 'string' && ACCESS_REVOCATION_ERRORS.has(value.detail)
  }

  private revokeAccess() {
    if (this.explicitlyClosed) return
    this.explicitlyClosed = true
    this.connectGeneration++
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer)
    this.reconnectTimer = null
    const socket = this.socket
    this.socket = null
    this.readySocket = null
    this.stopHeartbeat(socket)
    socket?.close()
    this.options.onAuthorizationFailure?.()
  }
}
