import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Tabs } from './Tabs'

const items = [
  { id: 'parks', label: 'Парки' },
  { id: 'users', label: 'Пользователи', count: 2 },
  { id: 'audit', label: 'Аудит' },
]

describe('Tabs', () => {
  it('marks the active tab with aria-selected', () => {
    render(<Tabs items={items} onChange={vi.fn()} value="users" />)

    expect(screen.getByRole('tab', { name: /Пользователи/ })).toHaveAttribute(
      'aria-selected',
      'true',
    )
    expect(screen.getByRole('tab', { name: 'Парки' })).toHaveAttribute('aria-selected', 'false')
  })

  it('shows optional tab counts', () => {
    render(<Tabs items={items} onChange={vi.fn()} value="users" />)
    const tab = screen.getByRole('tab', { name: /Пользователи/ })
    expect(within(tab).getByText('2')).toHaveClass('tab-count')
  })

  it('moves selection with arrow keys', () => {
    const onChange = vi.fn()
    render(<Tabs items={items} onChange={onChange} value="users" />)

    const activeTab = screen.getByRole('tab', { name: /Пользователи/ })
    fireEvent.keyDown(activeTab, { key: 'ArrowRight' })
    expect(onChange).toHaveBeenCalledWith('audit')

    fireEvent.keyDown(activeTab, { key: 'ArrowLeft' })
    expect(onChange).toHaveBeenCalledWith('parks')
  })

  it('selects a tab on click', () => {
    const onChange = vi.fn()
    render(<Tabs items={items} onChange={onChange} value="parks" />)

    fireEvent.click(screen.getByRole('tab', { name: 'Аудит' }))
    expect(onChange).toHaveBeenCalledWith('audit')
  })
})
