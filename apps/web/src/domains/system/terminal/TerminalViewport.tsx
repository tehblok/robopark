import { useEffect, useRef, useState } from 'react'
import { FitAddon } from '@xterm/addon-fit'
import { Terminal } from '@xterm/xterm'
import '@xterm/xterm/css/xterm.css'
import type { TerminalProfile, TerminalSessionState } from './terminalApi'
import { buildPlainTextSnapshot, MAX_SNAPSHOT_LINES } from './terminalSnapshot'

const encoder = new TextEncoder()
const MAX_INPUT_BYTES = 16 * 1024

type ViewportConnection = {
  sendInput(bytes: Uint8Array): void
  resize(cols: number, rows: number): void
  close(): void
  setOutputHandler?: (handler: (bytes: Uint8Array) => void) => () => void
}

export function TerminalViewport({ connection, profile, state, onTerminate }: {
  connection: ViewportConnection
  profile: TerminalProfile
  state: TerminalSessionState | 'connecting'
  onTerminate: () => void
}) {
  const host = useRef<HTMLDivElement>(null)
  const terminalRef = useRef<Terminal | null>(null)
  const [snapshot, setSnapshot] = useState('')
  const [paste, setPaste] = useState<string | null>(null)
  const [pasteError, setPasteError] = useState<string | null>(null)
  const [dimensions, setDimensions] = useState({ cols: 80, rows: 24 })

  useEffect(() => {
    const element = host.current
    if (!element) return
    const terminal = new Terminal({
      allowProposedApi: false, allowTransparency: false, convertEol: true,
      cursorBlink: true, disableStdin: false, scrollback: MAX_SNAPSHOT_LINES,
      theme: { background: profile === 'root' ? '#1d1115' : '#0d1513', foreground: '#f1f4ef' },
    })
    const fit = new FitAddon()
    terminal.loadAddon(fit)
    // Consume clipboard and hyperlink OSC sequences. No clipboard, web-links,
    // attach, image or custom link-provider addon is loaded.
    const clipboardOsc = terminal.parser.registerOscHandler(52, () => true)
    const hyperlinkOsc = terminal.parser.registerOscHandler(8, () => true)
    terminal.open(element)
    terminalRef.current = terminal
    const publishSnapshot = () => {
      const buffer = terminal.buffer.active
      const lines: string[] = []
      for (let index = Math.max(0, buffer.baseY + buffer.cursorY - MAX_SNAPSHOT_LINES + 1); index <= buffer.baseY + buffer.cursorY; index++) {
        const line = buffer.getLine(index)
        if (line) lines.push(line.translateToString(true))
      }
      setSnapshot(buildPlainTextSnapshot(lines))
    }
    const data = terminal.onData(value => {
      try { connection.sendInput(encoder.encode(value)) } catch { /* detached input is deliberately dropped */ }
    })
    const parsed = terminal.onWriteParsed(publishSnapshot)
    const releaseOutput = connection.setOutputHandler?.(bytes => terminal.write(bytes, publishSnapshot))
    const resize = typeof ResizeObserver === 'function' ? new ResizeObserver(() => {
      fit.fit()
      setDimensions({ cols: terminal.cols, rows: terminal.rows })
      connection.resize(terminal.cols, terminal.rows)
    }) : null
    resize?.observe(element)
    fit.fit()
    setDimensions({ cols: terminal.cols, rows: terminal.rows })
    connection.resize(terminal.cols, terminal.rows)
    terminal.focus()
    return () => {
      releaseOutput?.(); resize?.disconnect(); parsed.dispose(); data.dispose()
      clipboardOsc.dispose(); hyperlinkOsc.dispose(); fit.dispose(); terminal.dispose(); terminalRef.current = null
    }
  }, [connection, profile])

  const submitPaste = () => {
    if (paste !== null) {
      const bytes = encoder.encode(paste)
      if (bytes.byteLength > MAX_INPUT_BYTES) {
        setPasteError('Вставка превышает 16 КиБ. Сократите текст; он остался в окне для редактирования.')
        return
      }
      try { connection.sendInput(bytes) } catch {
        setPasteError('Соединение разорвано. Текст остался в окне; подключитесь и повторите.')
        return
      }
    }
    setPaste(null); setPasteError(null)
  }

  return <section className={`rp-terminal-viewport rp-terminal-viewport--${profile}`} aria-label={profile === 'root' ? 'Root терминал' : 'Обычный терминал'}>
    <div className="rp-terminal-controls">
      <strong>{profile === 'root' ? 'ROOT' : 'robopark-maint'}</strong>
      <span>{state}</span>
      <span aria-label="Размер терминала">{dimensions.cols} × {dimensions.rows}</span>
      <button type="button" onClick={() => { try { connection.sendInput(new Uint8Array([3])) } catch { /* detached */ } }}>Ctrl+C</button>
      <button type="button" onClick={() => { connection.close(); onTerminate() }}>Завершить</button>
    </div>
    <div
      className="rp-terminal-canvas" data-testid="terminal-canvas" ref={host} tabIndex={0}
      onCopy={event => event.preventDefault()} onCut={event => event.preventDefault()}
      onPaste={event => {
        event.preventDefault()
        const value = event.clipboardData.getData('text/plain')
        if (!value) return
        const oversized = encoder.encode(value).byteLength > MAX_INPUT_BYTES
        if (/\r|\n/.test(value) || oversized) {
          setPaste(value)
          setPasteError(oversized ? 'Вставка превышает 16 КиБ. Сократите текст; он остался в окне для редактирования.' : null)
        }
        else { try { connection.sendInput(encoder.encode(value)) } catch { /* detached */ } }
      }}
    />
    <pre className="rp-terminal-snapshot" role="log" aria-live="polite" aria-label="Текстовый вывод терминала">{snapshot}</pre>
    {paste !== null && <div role="dialog" aria-modal="true" aria-label={/\r|\n/.test(paste) ? 'Подтвердить многострочную вставку' : 'Подтвердить вставку'} className="rp-terminal-paste">
      <h2>{/\r|\n/.test(paste) ? 'Подтвердить многострочную вставку' : 'Подтвердить вставку'}</h2>
      <p>Команды будут отправлены в {profile === 'root' ? 'root' : 'обычный'} терминал.</p>
      {pasteError && <p role="alert">{pasteError}</p>}
      <pre>{paste.slice(0, 4096)}</pre>
      <button type="button" disabled={Boolean(pasteError)} onClick={submitPaste}>Вставить команды</button>
      <button type="button" onClick={() => { setPaste(null); setPasteError(null) }}>Отмена</button>
    </div>}
  </section>
}
