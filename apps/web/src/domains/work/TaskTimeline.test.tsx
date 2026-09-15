import { render, screen, within } from '@testing-library/react'
import { it, expect } from 'vitest'
import { TaskTimeline } from './TaskTimeline'

it('renders one chronological chat and keeps attachments inside their message', () => {
  render(<TaskTimeline items={[
    { id: 'later', kind: 'system', author: 'СУРП', text: 'Передано на проверку', created_at: '2026-09-15T10:02:00Z', sync_state: 'pending', attachments: [] },
    { id: 'first', kind: 'user', author: 'Анна', text: 'Заменила датчик', created_at: '2026-09-15T10:00:00Z', sync_state: 'synced', attachments: [{ id: 'photo', name: 'robot.jpg', url: 'https://tracker.example/robot.jpg', mimetype: 'image/jpeg' }] },
    { id: 'middle', kind: 'tracker', author: 'Бот', text: 'Принято', created_at: '2026-09-15T10:01:00Z', sync_state: 'saved', attachments: [] },
  ]} />)

  const messages = screen.getAllByRole('listitem')
  expect(messages.map(item => item.textContent)).toEqual(expect.arrayContaining([]))
  expect(messages[0]).toHaveTextContent('Заменила датчик')
  expect(messages[1]).toHaveTextContent('Принято')
  expect(messages[2]).toHaveTextContent('Передано на проверку')
  expect(within(messages[0]!).getByRole('link', { name: 'robot.jpg' })).toHaveAttribute('href', 'https://tracker.example/robot.jpg')
  expect(screen.queryByRole('heading', { name: 'История действий' })).not.toBeInTheDocument()
})
