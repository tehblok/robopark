import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { Blocker } from '../../api'
import { TaskList } from './TaskBoard'

const sample: Blocker = {
  key: 'ROBOPARK-42',
  summary: 'Robot stopped on route',
  status: 'Open',
  status_key: 'open',
  robot: '447',
  created_at: '2024-06-15T14:30:00+0000',
  hours_created: '5',
  url: 'https://st.yandex-team.ru/ROBOPARK-42',
  bucket: 'queued',
  priority: 'major',
  assignee: { display: 'Ivan Petrov', login: 'ivan' },
}

describe('TaskList', () => {
  it('renders a task card with key and summary', () => {
    render(<TaskList items={[sample]} onSelect={vi.fn()} />)

    expect(screen.getByText('ROBOPARK-42')).toBeInTheDocument()
    expect(screen.getByText('Robot stopped on route')).toBeInTheDocument()
    expect(screen.getByText('447')).toBeInTheDocument()
  })

  it('highlights the selected task', () => {
    render(<TaskList items={[sample]} onSelect={vi.fn()} selected="ROBOPARK-42" />)

    const row = screen.getByRole('button', { name: /ROBOPARK-42/i })
    expect(row).toHaveClass('is-selected')
    expect(row).toHaveAttribute('aria-current', 'true')
  })

  it('calls onSelect when a row is clicked', () => {
    const onSelect = vi.fn()
    render(<TaskList items={[sample]} onSelect={onSelect} />)

    fireEvent.click(screen.getByRole('button', { name: /ROBOPARK-42/i }))
    expect(onSelect).toHaveBeenCalledWith('ROBOPARK-42')
  })
})
