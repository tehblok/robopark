import { render, screen } from '@testing-library/react'
import { it, expect } from 'vitest'
import { TaskSyncStatus } from './TaskSyncStatus'

it('explains a missing Tracker transition without exposing unknown error text', () => {
  const view = render(<TaskSyncStatus state="needs_attention" errorCode="tracker_transition_missing" />)
  expect(screen.getByRole('status')).toHaveTextContent('В Трекере нет доступного перехода')
  view.rerender(<TaskSyncStatus state="needs_attention" errorCode="secret upstream error" />)
  expect(screen.getByRole('status')).not.toHaveTextContent('secret')
})

it('explains a stopped old-cycle action without implying it was delivered', () => {
  render(<TaskSyncStatus state="needs_attention" errorCode="task_already_closed" />)
  expect(screen.getByRole('status')).toHaveTextContent('не будет отправлено в новый цикл ремонта')
})

it('points to the operator Tracker login when assignment fails', () => {
  render(<TaskSyncStatus state="needs_attention" errorCode="tracker_operator_assignment_failed" />)
  expect(screen.getByRole('status')).toHaveTextContent('логин оператора в Tracker')
})

it.each([
  ['needs_attention', 'Нужно внимание'],
] as const)('renders %s as plain-language synchronization status', (state, label) => {
  render(<TaskSyncStatus state={state} />)
  expect(screen.getByRole('status')).toHaveTextContent(label)
})
it.each(['saved', 'synced', 'pending'] as const)('does not clutter the task with %s status', state => {
  const { container } = render(<TaskSyncStatus state={state} />)
  expect(container).toBeEmptyDOMElement()
})
