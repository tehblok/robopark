import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { TerminalViewport } from './TerminalViewport'
import { buildPlainTextSnapshot } from './terminalSnapshot'

const { terminal, handlers } = vi.hoisted(() => {
  const handlers = { data: null as null | ((value: string) => void), write: null as null | (() => void) }
  const line = (value: string) => ({ translateToString: () => value })
  const terminal = {
    cols: 80, rows: 24,
    buffer: { active: { baseY: 0, cursorY: 1, getLine: (index: number) => line(index === 0 ? '<img src=x onerror=alert(1)>' : 'safe') } },
    parser: { registerOscHandler: vi.fn(() => ({ dispose: vi.fn() })) },
    loadAddon: vi.fn(), open: vi.fn(), dispose: vi.fn(), focus: vi.fn(),
    onData: vi.fn((callback: (value: string) => void) => { handlers.data = callback; return { dispose: vi.fn() } }),
    onWriteParsed: vi.fn((callback: () => void) => { handlers.write = callback; return { dispose: vi.fn() } }),
    write: vi.fn((_value: Uint8Array, callback?: () => void) => callback?.()), clear: vi.fn(),
    attachCustomKeyEventHandler: vi.fn(),
  }
  return { terminal, handlers }
})

vi.mock('@xterm/xterm', () => ({ Terminal: class { constructor() { return terminal } } }))
vi.mock('@xterm/addon-fit', () => ({ FitAddon: class { fit = vi.fn(); dispose = vi.fn() } }))

beforeEach(() => { vi.clearAllMocks(); handlers.data = null; handlers.write = null })

it('renders parsed output as plain text and consumes dangerous OSC handlers', async () => {
  const connection = { sendInput: vi.fn(), resize: vi.fn(), close: vi.fn() }
  const { container } = render(<TerminalViewport connection={connection} profile="maintenance" state="active" onTerminate={vi.fn()} />)
  handlers.write?.()
  await waitFor(() => expect(screen.getByRole('log')).toHaveTextContent('<img src=x onerror=alert(1)>'))
  expect(container.querySelector('img')).toBeNull()
  expect(terminal.parser.registerOscHandler).toHaveBeenCalledWith(52, expect.any(Function))
  expect(terminal.parser.registerOscHandler).toHaveBeenCalledWith(8, expect.any(Function))
  expect(navigator.clipboard?.writeText).toBeUndefined()
})

it('previews multiline paste and sends it only after confirmation', () => {
  const connection = { sendInput: vi.fn(), resize: vi.fn(), close: vi.fn() }
  render(<TerminalViewport connection={connection} profile="root" state="active" onTerminate={vi.fn()} />)
  fireEvent.paste(screen.getByTestId('terminal-canvas'), { clipboardData: { getData: () => 'echo one\necho two\n' } })
  expect(screen.getByRole('dialog', { name: 'Подтвердить многострочную вставку' })).toBeVisible()
  expect(connection.sendInput).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Вставить команды' }))
  expect(connection.sendInput).toHaveBeenCalledWith(new TextEncoder().encode('echo one\necho two\n'))
})

it('keeps an oversized single-line paste visible and explains the frame limit', () => {
  const connection = { sendInput: vi.fn(), resize: vi.fn(), close: vi.fn() }
  render(<TerminalViewport connection={connection} profile="maintenance" state="active" onTerminate={vi.fn()} />)
  fireEvent.paste(screen.getByTestId('terminal-canvas'), { clipboardData: { getData: () => 'x'.repeat(16 * 1024 + 1) } })

  expect(screen.getByRole('dialog', { name: 'Подтвердить вставку' })).toBeVisible()
  expect(screen.getByRole('alert')).toHaveTextContent('превышает 16 КиБ')
  fireEvent.click(screen.getByRole('button', { name: 'Вставить команды' }))
  expect(connection.sendInput).not.toHaveBeenCalled()
  expect(screen.getByRole('dialog', { name: 'Подтвердить вставку' })).toBeVisible()
})

it('bounds the accessible snapshot to 2000 lines and 256 KiB', () => {
  const lines = Array.from({ length: 2500 }, (_, index) => `${index}:${'x'.repeat(200)}`)
  const snapshot = buildPlainTextSnapshot(lines)
  expect(snapshot.split('\n').length).toBeLessThanOrEqual(2000)
  expect(new TextEncoder().encode(snapshot).byteLength).toBeLessThanOrEqual(256 * 1024)
})
