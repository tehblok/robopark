import type { ComponentProps } from 'react'
import { render, screen } from '@testing-library/react'
import { describe, expect, expectTypeOf, it, vi } from 'vitest'
import { RepairSla } from './RepairSla'

describe('RepairSla', () => {
  it('does not claim the queue start is missing when the park timezone is unconfirmed', () => {
    render(<RepairSla
      deadline={null}
      queuedAt="2026-09-29T09:00:00Z"
      source="status_history"
      now={Date.parse('2026-09-29T10:00:00Z')}
      timezone="Europe/Moscow"
    />)
    expect(screen.getByRole('status')).toHaveAttribute('title', expect.stringContaining('Часовой пояс парка при входе в очередь не подтверждён'))
  })

  it('shows the remaining hours and minutes at a fixed time', () => {
    render(<RepairSla deadline="2026-09-15T12:00:00Z" source="status_history" now={Date.parse('2026-09-15T10:30:00Z')} timezone="Europe/Moscow" />)
    expect(screen.getByRole('status')).toHaveTextContent('SLA: 1:30')
  })

  it('shows how long an SLA is overdue', () => {
    render(<RepairSla deadline="2026-09-15T12:00:00Z" source="status_history" now={Date.parse('2026-09-15T12:20:00Z')} timezone="Europe/Moscow" />)
    expect(screen.getByRole('status')).toHaveTextContent('SLA: 0:00 · Просрочено')
  })

  it('does not count hours outside the Moscow working window', () => {
    render(<RepairSla deadline="2026-09-19T10:00:00Z" source="status_history" now={Date.parse('2026-09-18T17:00:00Z')} timezone="Europe/Moscow" />)
    expect(screen.getAllByRole('status')).toHaveLength(1)
    expect(screen.getByRole('status')).toHaveTextContent('SLA: 5:00')
  })

  it('shows confirmed consumed SLA hours separately from overnight downtime', () => {
    render(<RepairSla
      deadline="2026-09-28T11:00:00Z"
      now={Date.parse('2026-09-28T02:30:00Z')}
      queuedAt="2026-09-27T23:15:00Z"
      source="status_history"
      timezone="Europe/Moscow"
    />)
    expect(screen.getByRole('status')).toHaveTextContent('SLA: 5:00')
    expect(screen.getByRole('status')).toHaveTextContent('SLA: 5:00')
  })

  it('marks a passed 21:00 deadline without claiming zero overdue hours', () => {
    render(<RepairSla deadline="2026-09-18T18:00:00Z" now={Date.parse('2026-09-18T18:01:00Z')} timezone="Europe/Moscow" />)
    expect(screen.getByRole('status')).toHaveTextContent('SLA: 0:00 · Просрочено')
    expect(screen.getByRole('status')).not.toHaveTextContent('0.0 ч')
  })

  it('shows the exact deadline without marking the task late', () => {
    render(<RepairSla deadline="2026-09-18T18:00:00Z" now={Date.parse('2026-09-18T18:00:00Z')} timezone="Europe/Moscow" />)
    expect(screen.getByRole('status')).toHaveTextContent('SLA: 0:00 · Срок наступил')
  })

  it('does not create a timer for each rendered card', () => {
    const interval = vi.spyOn(globalThis, 'setInterval')
    render(<><RepairSla deadline={null} source={null} now={0} timezone="Europe/Moscow" /><RepairSla deadline={null} source={null} now={0} timezone="Europe/Moscow" /></>)
    expect(interval).not.toHaveBeenCalled()
    interval.mockRestore()
  })

  it('requires one controlled clock for every mounted SLA', () => {
    expectTypeOf<ComponentProps<typeof RepairSla>['now']>().toEqualTypeOf<number>()
    const now = Date.parse('2026-09-15T10:30:00Z')
    render(<><RepairSla deadline="2026-09-15T12:00:00Z" now={now} timezone="Europe/Moscow" /><RepairSla deadline="2026-09-15T12:00:00Z" now={now} timezone="Europe/Moscow" /></>)
    expect(screen.getAllByRole('status').map(item => item.textContent)).toEqual(['SLA: 1:30', 'SLA: 1:30'])
  })

  it('uses the park time zone when work resumes the next day', () => {
    render(<RepairSla deadline="2026-09-15T04:00:00Z" now={Date.parse('2026-09-14T14:00:00Z')} timezone="Asia/Novosibirsk" />)
    expect(screen.getByRole('status')).toHaveTextContent('SLA: 2:00')
  })
})
