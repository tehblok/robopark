import { createRef, type ReactElement } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { BottomSheet } from './BottomSheet'
import { Dialog } from './Dialog'

function renderInApp(ui: ReactElement) {
  const root = document.createElement('div')
  root.id = 'root'
  document.body.append(root)
  return { root, ...render(ui, { container: root }) }
}

afterEach(() => {
  document.querySelectorAll('.dialog-test-opener').forEach((element) => element.remove())
  document.body.style.overflow = ''
})

describe('Dialog', () => {
  it('moves focus into a portalled dialog, locks the page, closes on Escape, and restores focus', () => {
    const opener = document.createElement('button')
    opener.className = 'dialog-test-opener'
    opener.textContent = 'Открыть'
    document.body.append(opener)
    opener.focus()
    document.body.style.overflow = 'clip'
    const onOpenChange = vi.fn()
    const { rerender, root } = renderInApp(
      <Dialog open onOpenChange={onOpenChange} title="Запросить парк">
        <button type="button">Первое действие</button>
        <button type="button">Последнее действие</button>
      </Dialog>,
    )

    const dialog = screen.getByRole('dialog')
    expect(root).not.toContainElement(dialog)
    expect(screen.getByRole('button', { name: 'Первое действие' })).toHaveFocus()
    expect(root).toHaveAttribute('inert')
    expect(document.body).toHaveStyle({ overflow: 'hidden' })

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onOpenChange).toHaveBeenCalledWith(false)

    rerender(
      <Dialog open={false} onOpenChange={onOpenChange} title="Запросить парк">
        <button type="button">Первое действие</button>
      </Dialog>,
    )
    expect(root).not.toHaveAttribute('inert')
    expect(document.body).toHaveStyle({ overflow: 'clip' })
    expect(opener).toHaveFocus()
  })

  it('wraps Tab and Shift+Tab between the dialog controls', () => {
    renderInApp(
      <Dialog dismissible={false} open onOpenChange={vi.fn()} title="Действия">
        <button type="button">Первое</button>
        <button type="button">Последнее</button>
      </Dialog>,
    )
    const first = screen.getByRole('button', { name: 'Первое' })
    const last = screen.getByRole('button', { name: 'Последнее' })

    last.focus()
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(first).toHaveFocus()

    first.focus()
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })
    expect(last).toHaveFocus()
  })

  it('prefers the requested initial focus target', () => {
    const initialFocusRef = createRef<HTMLButtonElement>()
    renderInApp(
      <Dialog
        dismissible={false}
        initialFocusRef={initialFocusRef}
        open
        onOpenChange={vi.fn()}
        title="Назначить фокус"
      >
        <button type="button">Первое</button>
        <button ref={initialFocusRef} type="button">Выбранное</button>
      </Dialog>,
    )

    expect(screen.getByRole('button', { name: 'Выбранное' })).toHaveFocus()
  })

  it('ignores Escape when the dialog is mandatory', () => {
    const onOpenChange = vi.fn()
    renderInApp(
      <Dialog dismissible={false} open onOpenChange={onOpenChange} title="Техработы">
        <button type="button">Ожидать</button>
      </Dialog>,
    )

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onOpenChange).not.toHaveBeenCalled()
  })

  it('focuses the dialog panel when a mandatory dialog has no controls', () => {
    renderInApp(
      <Dialog
        dismissible={false}
        onOpenChange={vi.fn()}
        open
        role="alertdialog"
        title="Техработы"
      >
        <p>Ожидайте</p>
      </Dialog>,
    )

    expect(screen.getByRole('alertdialog')).toHaveFocus()
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(screen.getByRole('alertdialog')).toHaveFocus()
  })
})

describe('BottomSheet', () => {
  it('keeps the dialog focus and dismissal contract with sheet presentation', () => {
    const onOpenChange = vi.fn()
    renderInApp(
      <BottomSheet open onOpenChange={onOpenChange} title="Детали робота">
        <button type="button">Продолжить</button>
      </BottomSheet>,
    )

    expect(screen.getByRole('button', { name: 'Продолжить' })).toHaveFocus()
    expect(document.querySelector('.rp-bottom-sheet')).toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })
})
