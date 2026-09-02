import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { Button, IconButton } from './Button'

describe('Button', () => {
  it('disables a busy action while preserving its accessible name', () => {
    render(<Button busy>Сохранить</Button>)

    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Сохранить' })).toHaveAttribute('aria-busy', 'true')
  })

  it('does not invoke a busy action', async () => {
    const onClick = vi.fn()
    const user = userEvent.setup()
    render(<Button busy onClick={onClick}>Сохранить</Button>)

    await user.click(screen.getByRole('button', { name: 'Сохранить' }))

    expect(onClick).not.toHaveBeenCalled()
  })
})

describe('IconButton', () => {
  it('exposes its required assistive label', () => {
    render(<IconButton icon="refresh" label="Обновить данные" />)

    expect(screen.getByRole('button', { name: 'Обновить данные' })).toBeVisible()
  })

  it('preserves a caller class alongside its icon-button class', () => {
    render(<IconButton className="toolbar-refresh" icon="refresh" label="Обновить" />)

    expect(screen.getByRole('button', { name: 'Обновить' })).toHaveClass(
      'rp-icon-button',
      'toolbar-refresh',
    )
  })
})
