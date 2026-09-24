/// <reference types="node" />

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
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

  it('keeps grouped buttons aligned and limits full-width primary actions to the mobile modifier', () => {
    const css = readFileSync(resolve('src/design-system/actions/Button.css'), 'utf8')

    expect(css).toMatch(/\.rp-button\s*\{[^}]*gap:\s*var\(--rp-control-gap\)/s)
    expect(css).toMatch(/\.rp-button--compact\s*\{[^}]*padding-inline:\s*var\(--rp-compact-row-padding\)/s)
    expect(css).toMatch(/\.rp-action-bar\s*\{[^}]*align-items:\s*stretch[^}]*display:\s*flex[^}]*gap:\s*var\(--rp-action-gap\)/s)
    expect(css).toMatch(/\.rp-action-bar > \.rp-button\s*\{[^}]*min-height:\s*var\(--rp-control-min-size\)/s)
    expect(css).toMatch(/@media \(max-width:\s*599px\)[\s\S]*\.rp-action-bar--mobile-primary > \.rp-button--primary\s*\{[^}]*min-height:\s*var\(--rp-touch-primary-min-size\)[^}]*width:\s*100%/s)
    expect(css).not.toMatch(/^\.rp-button--primary\s*\{[^}]*width:\s*100%/ms)
  })

  it('keeps primary, secondary, and destructive actions visually distinct', () => {
    const css = readFileSync(resolve('src/design-system/actions/Button.css'), 'utf8')

    expect(css).toMatch(/\.rp-button--primary\s*\{[^}]*background:\s*var\(--rp-action\)[^}]*color:\s*var\(--rp-action-on\)/s)
    expect(css).toMatch(/\.rp-button--secondary\s*\{[^}]*background:\s*var\(--rp-surface\)[^}]*border-color:\s*var\(--rp-border\)[^}]*color:\s*var\(--rp-text\)/s)
    expect(css).toMatch(/\.rp-button--danger\s*\{[^}]*background:\s*var\(--rp-critical\)[^}]*color:\s*var\(--rp-critical-on\)/s)
  })

  it('stacks two secondary actions on mobile while retaining desktop wrapping', () => {
    const { container } = render(
      <div className="rp-action-bar">
        <Button variant="secondary">Отмена</Button>
        <Button variant="secondary">Сохранить черновик</Button>
      </div>,
    )
    const css = readFileSync(resolve('src/design-system/actions/Button.css'), 'utf8')
    const actionBar = container.querySelector('.rp-action-bar')

    expect(actionBar).toContainElement(screen.getByRole('button', { name: 'Отмена' }))
    expect(actionBar).toContainElement(screen.getByRole('button', { name: 'Сохранить черновик' }))
    expect(screen.getAllByRole('button')).toHaveLength(2)
    expect(screen.getAllByRole('button').every(button => button.classList.contains('rp-button--secondary'))).toBe(true)
    expect(actionBar).not.toHaveClass('rp-action-bar--mobile-primary')
    expect(css).toMatch(/\.rp-action-bar\s*\{[^}]*flex-wrap:\s*wrap/s)
    expect(css).toMatch(/@media \(max-width:\s*599px\)\s*\{[^}]*\.rp-action-bar\s*\{[^}]*align-items:\s*flex-start[^}]*flex-direction:\s*column[^}]*flex-wrap:\s*nowrap/s)
    expect(css).not.toMatch(/@media \(max-width:\s*599px\)[\s\S]*\.rp-action-bar(?:\s|>)[^{]*\{[^}]*width:\s*100%/s)
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
