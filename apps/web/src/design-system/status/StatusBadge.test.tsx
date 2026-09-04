import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { StatusBadge, type StatusTone } from './StatusBadge'

describe('StatusBadge', () => {
  it('keeps critical status text visible alongside its semantics', () => {
    render(<StatusBadge tone="critical">Критично</StatusBadge>)

    expect(screen.getByText('Критично')).toHaveAttribute('data-tone', 'critical')
  })

  it.each([
    ['neutral', 'lucide-info'],
    ['info', 'lucide-info'],
    ['success', 'lucide-circle-check'],
    ['warning', 'lucide-triangle-alert'],
    ['critical', 'lucide-circle-alert'],
  ] satisfies Array<[StatusTone, string]>)('uses the %s tone default icon', (tone, icon) => {
    render(<StatusBadge tone={tone}>{tone}</StatusBadge>)

    expect(screen.getByText(tone).querySelector(`.${icon}`)).toBeInTheDocument()
  })

  it('uses an explicitly supplied icon instead of the tone default', () => {
    render(<StatusBadge icon="offline" tone="warning">Нет связи</StatusBadge>)

    const badge = screen.getByText('Нет связи')
    expect(badge.querySelector('.lucide-wifi-off')).toBeInTheDocument()
    expect(badge.querySelector('.lucide-triangle-alert')).not.toBeInTheDocument()
  })
})
