import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { WorkFilters } from './WorkFilters'

describe('WorkFilters', () => {
  it('applies all form values and resets pagination', () => {
    const onApply = vi.fn()
    render(
      <WorkFilters
        allowUntagged
        loading={false}
        onApply={onApply}
        trackerLogin="ivan"
        value={{ filters: { queue: 'ROBOPARK' }, sort: 'oldest', page: 4 }}
      />,
    )

    fireEvent.change(screen.getByLabelText('Статус'), { target: { value: 'open' } })
    fireEvent.change(screen.getByLabelText('Робот'), { target: { value: '447' } })
    fireEvent.change(screen.getByLabelText('Сортировка'), { target: { value: 'newest' } })
    fireEvent.click(screen.getByRole('button', { name: 'Применить фильтры' }))

    expect(onApply).toHaveBeenCalledWith({
      filters: { queue: 'ROBOPARK', status: 'open', robot: '447' },
      sort: 'newest',
      page: 1,
    })
  })

  it('resets the draft to the incoming URL state', () => {
    render(
      <WorkFilters
        allowUntagged
        loading={false}
        onApply={vi.fn()}
        value={{
          filters: { queue: 'ROBOPARK', status: 'ready', robot: '1555' },
          sort: 'oldest',
          page: 3,
        }}
      />,
    )

    fireEvent.change(screen.getByLabelText('Статус'), { target: { value: 'closed' } })
    fireEvent.change(screen.getByLabelText('Робот'), { target: { value: '447' } })
    fireEvent.change(screen.getByLabelText('Сортировка'), { target: { value: 'newest' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сбросить фильтры' }))

    expect(screen.getByLabelText('Статус')).toHaveValue('ready')
    expect(screen.getByLabelText('Робот')).toHaveValue('1555')
    expect(screen.getByLabelText('Сортировка')).toHaveValue('oldest')
  })

  it('does not expose or submit the untagged scope when it is not allowed', () => {
    const onApply = vi.fn()
    render(
      <WorkFilters
        allowUntagged={false}
        loading={false}
        onApply={onApply}
        value={{
          filters: { queue: 'ROBOPARK', untagged: true },
          sort: 'oldest',
          page: 1,
        }}
      />,
    )

    expect(screen.queryByRole('checkbox', { name: 'Без тега парка' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Применить фильтры' }))

    expect(onApply).toHaveBeenCalledWith({
      filters: { queue: 'ROBOPARK' },
      sort: 'oldest',
      page: 1,
    })
  })

  it('fails closed instead of submitting an invalid age filter', () => {
    const onApply = vi.fn()
    render(
      <WorkFilters
        allowUntagged
        loading={false}
        onApply={onApply}
        value={{ filters: { queue: 'ROBOPARK' }, sort: 'oldest', page: 1 }}
      />,
    )

    fireEvent.change(screen.getByLabelText('Старше, часов'), {
      target: { value: '9007199254740992' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Применить фильтры' }))

    expect(onApply).toHaveBeenCalledWith({
      filters: { queue: 'ROBOPARK' },
      sort: 'oldest',
      page: 1,
    })
  })
})
