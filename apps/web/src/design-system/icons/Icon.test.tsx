/// <reference types="node" />

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Icon } from './Icon'

describe('Icon', () => {
  it('renders the checked wheeled platform glyph as decorative content', () => {
    render(<Icon name="robot-check" data-testid="icon" />)

    expect(screen.getByTestId('icon')).toHaveAttribute('aria-hidden', 'true')
    expect(screen.getByTestId('icon')).toHaveAttribute('data-rp-glyph', 'robot-check')
  })

  it('renders the wheeled platform glyph with the inherited text color', () => {
    render(<Icon name="robot" data-testid="robot-icon" />)

    expect(screen.getByTestId('robot-icon')).toHaveAttribute('data-rp-glyph', 'robot')
    expect(screen.getByTestId('robot-icon')).toHaveAttribute('stroke', 'currentColor')
  })

  it('keeps general Lucide symbols out of the accessibility tree', () => {
    render(<Icon name="refresh" data-testid="refresh-icon" />)

    expect(screen.getByTestId('refresh-icon')).toHaveAttribute('aria-hidden', 'true')
    expect(screen.getByTestId('refresh-icon')).toHaveAttribute('focusable', 'false')
  })

  it('keeps robot glyph source code-native and free of commercial payloads', () => {
    const source = readFileSync(resolve('src/design-system/icons/robotGlyphs.tsx'), 'utf8')

    expect(source).not.toMatch(/yandex/i)
    expect(source).not.toMatch(/https?:\/\//i)
    expect(source).not.toMatch(/<image\b/i)
    expect(source).not.toMatch(/base64/i)
    expect(source).not.toMatch(/<text\b/i)
  })
})
