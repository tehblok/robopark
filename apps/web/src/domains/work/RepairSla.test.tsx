import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { RepairSla } from './RepairSla'

describe('RepairSla', () => {
  it('shows the remaining hours and minutes at a fixed time', () => {
    render(<RepairSla deadline="2026-09-15T12:00:00Z" source="status_history" now={Date.parse('2026-09-15T10:30:00Z')} />)
    expect(screen.getByRole('status')).toHaveTextContent('Осталось 1.5 ч')
  })

  it('shows how long an SLA is overdue', () => {
    render(<RepairSla deadline="2026-09-15T12:00:00Z" source="status_history" now={Date.parse('2026-09-15T12:20:00Z')} />)
    expect(screen.getByRole('status')).toHaveTextContent('Просрочено на 0.3 ч')
  })

  it('does not count hours outside the Moscow working window', () => {
    render(<RepairSla deadline="2026-09-19T10:00:00Z" source="status_history" now={Date.parse('2026-09-18T17:00:00Z')} />)
    expect(screen.getAllByRole('status')).toHaveLength(1)
    expect(screen.getByRole('status')).toHaveTextContent('Осталось 5.0 ч')
  })

  it('does not create a timer for each rendered card', () => {
    const interval = vi.spyOn(globalThis, 'setInterval')
    render(<><RepairSla deadline={null} source={null} now={0} /><RepairSla deadline={null} source={null} now={0} /></>)
    expect(interval).not.toHaveBeenCalled()
    interval.mockRestore()
  })
})
