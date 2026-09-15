import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { WorkFilters } from './WorkFilters'

describe('WorkFilters', () => {
  it('keeps a fixed queue context without a manual status control', () => {
    render(<WorkFilters loading={false} onApply={vi.fn()} value={{ filters: { queue: 'RP' }, sort: 'oldest', page: 1 }} />)
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.getByText('В очереди')).toBeVisible()
    expect(screen.getByText('От старых к новым')).toBeVisible()
  })

  it('makes linked restrictions visible and clears them without losing park or status', () => {
    const onApply = vi.fn()
    render(<WorkFilters loading={false} onApply={onApply} value={{ filters: { queue: 'RP', status: 'new', robot: '447', assignee: 'ivan', ageHours: 24, untagged: true }, sort: 'oldest', page: 3 }} />)
    expect(screen.getByText('Робот: 447')).toBeVisible()
    expect(screen.getByText('Ответственный: ivan')).toBeVisible()
    expect(screen.getByText('Старше 24 ч')).toBeVisible()
    expect(screen.getByText('Без тега парка')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Сбросить ограничения' }))
    expect(onApply).toHaveBeenCalledWith({ filters: { queue: 'RP', status: 'new' }, sort: 'oldest', page: 1 })
  })

  it('shows a nondefault deep-link status as read-only context', () => {
    render(<WorkFilters loading={false} onApply={vi.fn()} value={{ filters: { queue: 'RP', status: 'review' }, sort: 'oldest', page: 1 }} />)
    expect(screen.getByText('Статус из ссылки: review')).toBeVisible()
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  })
})
