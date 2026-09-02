import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ConfirmDialog, type ConfirmDialogProps } from './ConfirmDialog'

function renderConfirmElement(props: Partial<ConfirmDialogProps> = {}) {
  return (
    <ConfirmDialog
      confirmLabel="Удалить"
      description="Действие нельзя отменить."
      onConfirm={vi.fn()}
      onOpenChange={vi.fn()}
      open
      title="Удалить парк"
      tone="danger"
      {...props}
    />
  )
}

function renderConfirm(props: Partial<ConfirmDialogProps> = {}) {
  return render(renderConfirmElement(props))
}

describe('ConfirmDialog', () => {
  it('requires the exact protected phrase before enabling the danger action', async () => {
    const user = userEvent.setup()
    renderConfirm({ confirmationPhrase: 'DELETE-PARK' })
    const input = screen.getByRole('textbox')
    const confirm = screen.getByRole('button', { name: 'Удалить' })

    expect(confirm).toBeDisabled()
    await user.type(input, 'delete-park')
    expect(confirm).toBeDisabled()
    await user.clear(input)
    await user.type(input, 'DELETE-PARK')
    expect(confirm).toBeEnabled()
  })

  it('disables both actions while confirmation is pending', () => {
    renderConfirm({ pending: true })

    expect(screen.getByRole('button', { name: 'Отмена' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Удалить' })).toBeDisabled()
  })

  it('announces a mutation error as a live alert', () => {
    renderConfirm({ error: 'Парк не удалён' })

    expect(screen.getByRole('alert')).toHaveTextContent('Парк не удалён')
    expect(screen.getByRole('alert')).toHaveAttribute('aria-live', 'assertive')
  })

  it('invokes confirmation without closing controlled state', async () => {
    const user = userEvent.setup()
    const onConfirm = vi.fn().mockResolvedValue(undefined)
    const onOpenChange = vi.fn()
    renderConfirm({ onConfirm, onOpenChange })

    await user.click(screen.getByRole('button', { name: 'Удалить' }))

    expect(onConfirm).toHaveBeenCalledOnce()
    expect(onOpenChange).not.toHaveBeenCalled()
    expect(screen.getByRole('alertdialog')).toBeInTheDocument()
  })

  it('clears a typed phrase after close and when the protected entity changes', async () => {
    const user = userEvent.setup()
    const { rerender } = renderConfirm({ open: true, confirmationPhrase: 'DELETE-A' })
    await user.type(screen.getByRole('textbox'), 'DELETE-A')
    expect(screen.getByRole('button', { name: 'Удалить' })).toBeEnabled()

    rerender(renderConfirmElement({ open: false, confirmationPhrase: 'DELETE-A' }))
    rerender(renderConfirmElement({ open: true, confirmationPhrase: 'DELETE-B' }))

    expect(screen.getByRole('textbox')).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Удалить' })).toBeDisabled()
  })
})
