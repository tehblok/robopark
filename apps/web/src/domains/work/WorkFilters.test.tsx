import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { WorkFilters } from './WorkFilters'

describe('WorkFilters', () => {
  it('labels the mechanic all-status view with its queue priority', () => {
    render(<WorkFilters queueFirst loading={false} onApply={vi.fn()} value={{ filters: { queue: 'RP' }, sort: 'oldest', page: 1 }} />)
    expect(screen.getByText('Сначала очередь · затем остальные по возрасту')).toBeVisible()
  })

  it('defaults the viewing-status selector to queued without changing ordering or queue scope', () => {
    render(<WorkFilters loading={false} onApply={vi.fn()} value={{ filters: { queue: 'RP', status: 'queued' }, sort: 'oldest', page: 1 }} />)
    expect(screen.getByRole('combobox', { name: 'Статус задач' })).toHaveValue('queued')
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'В очереди' })).toBeVisible()
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

  it('keeps a nondefault deep-link status selected', () => {
    render(<WorkFilters loading={false} onApply={vi.fn()} value={{ filters: { queue: 'RP', status: 'review' }, sort: 'oldest', page: 1 }} />)
    expect(screen.getByRole('combobox', { name: 'Статус задач' })).toHaveValue('review')
    expect(screen.getByRole('option', { name: 'review' })).toBeVisible()
  })

  it('shows a readable label for a known Tracker status from a deep link', () => {
    render(<WorkFilters loading={false} onApply={vi.fn()} value={{ filters: { queue: 'RP', status: 'open' }, sort: 'oldest', page: 1 }} />)
    expect(screen.getByRole('combobox', { name: 'Статус задач' })).toHaveValue('open')
    expect(screen.getByRole('option', { name: 'Открыта' })).toBeVisible()
  })

  it('changes only the viewed status and returns to page one', () => {
    const onApply = vi.fn()
    render(<WorkFilters loading={false} onApply={onApply} value={{ filters: { queue: 'RP', status: 'queued', robot: '447', assignee: 'ivan' }, sort: 'oldest', page: 3 }} />)

    fireEvent.change(screen.getByRole('combobox', { name: 'Статус задач' }), { target: { value: 'diagnostics' } })
    expect(onApply).toHaveBeenCalledWith({ filters: { queue: 'RP', status: 'diagnostics', robot: '447', assignee: 'ivan' }, sort: 'oldest', page: 1 })
  })

  it('limits driver viewing statuses to the API-permitted stages', () => {
    render(<WorkFilters driver loading={false} onApply={vi.fn()} value={{ filters: { queue: 'RP', status: 'new' }, sort: 'oldest', page: 1 }} />)

    expect(screen.getAllByRole('option').map(option => option.getAttribute('value'))).toEqual(['all', 'new', 'moving'])
  })

  it('lets only managers include hidden tasks through an explicit filter', () => {
    const onApply = vi.fn()
    const value = { filters: { queue: 'RP' }, sort: 'oldest' as const, page: 2 }
    const view = render(<WorkFilters loading={false} manager onApply={onApply} value={value} />)

    fireEvent.click(screen.getByRole('checkbox', { name: 'Показать скрытые задачи' }))
    expect(onApply).toHaveBeenCalledWith({
      filters: { queue: 'RP', includeHidden: true }, sort: 'oldest', page: 1,
    })

    view.rerender(<WorkFilters loading={false} onApply={onApply} value={value} />)
    expect(screen.queryByRole('checkbox', { name: 'Показать скрытые задачи' })).not.toBeInTheDocument()
  })
})
