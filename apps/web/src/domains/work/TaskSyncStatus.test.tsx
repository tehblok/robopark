import { render, screen } from '@testing-library/react'
import { it, expect } from 'vitest'
import { TaskSyncStatus } from './TaskSyncStatus'

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
