import { render, screen } from '@testing-library/react'
import { it, expect } from 'vitest'
import { TaskSyncStatus } from './TaskSyncStatus'

it('explains a missing Tracker transition without exposing unknown error text', () => {
  const view = render(<TaskSyncStatus state="needs_attention" errorCode="tracker_transition_missing" />)
  expect(screen.getByRole('status')).toHaveTextContent('В Трекере нет доступного перехода')
  view.rerender(<TaskSyncStatus state="needs_attention" errorCode="secret upstream error" />)
  expect(screen.getByRole('status')).not.toHaveTextContent('secret')
})

it.each([
  ['pending', 'Отправляется в Tracker'],
  ['needs_attention', 'Требует внимания'],
] as const)('renders %s as plain-language synchronization status', (state, label) => {
  render(<TaskSyncStatus state={state} />)
  expect(screen.getByRole('status')).toHaveTextContent(label)
})
it.each(['saved', 'synced'] as const)('does not clutter the task with %s status', state => {
  const { container } = render(<TaskSyncStatus state={state} />)
  expect(container).toBeEmptyDOMElement()
})
