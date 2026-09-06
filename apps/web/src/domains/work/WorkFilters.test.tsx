import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { WorkFilters } from './WorkFilters'

describe('WorkFilters', () => {
  it.each(['new', 'moving', 'queued', 'diagnostics', 'waiting_team', 'waiting_parts', 'inProgress'])('immediately applies open status %s and resets page with oldest sorting', (status) => {
    const onApply = vi.fn()
    render(<WorkFilters loading={false} onApply={onApply} value={{ filters: { queue: 'RP' }, sort: 'newest', page: 2 }} />)
    fireEvent.change(screen.getByLabelText('Статус открытых блокеров'), { target: { value: status } })
    expect(onApply).toHaveBeenCalledWith({ filters: { queue: 'RP', status }, sort: 'oldest', page: 1 })
  })

  it('exposes only a status selector and excludes completed statuses', () => {
    render(<WorkFilters loading={false} onApply={vi.fn()} value={{ filters: { queue: 'RP' }, sort: 'oldest', page: 1 }} />)
    expect(screen.getAllByRole('combobox')).toHaveLength(1)
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.queryByRole('option', { name: /Закрытые|Решённые/ })).not.toBeInTheDocument()
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

  it('returns to all open blockers without losing linked robot context', () => {
    const onApply = vi.fn()
    render(<WorkFilters loading={false} onApply={onApply} value={{ filters: { queue: 'RP', status: 'new', robot: '447' }, sort: 'oldest', page: 4 }} />)
    fireEvent.change(screen.getByLabelText('Статус открытых блокеров'), { target: { value: '' } })
    expect(onApply).toHaveBeenCalledWith({ filters: { queue: 'RP', robot: '447' }, sort: 'oldest', page: 1 })
  })

  it('offers drivers only their permitted workflow statuses', () => {
    render(<WorkFilters driver loading={false} onApply={vi.fn()} value={{ filters: { queue: 'RP' }, sort: 'oldest', page: 1 }} />)
    expect(screen.getAllByRole('option').map(option => option.getAttribute('value'))).toEqual(['', 'new', 'moving', 'open'])
  })
})
