import { render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, it, expect, vi } from 'vitest'
import type { TaskTimelineItem } from '../../api'
import { activateTaskAttachmentCache, clearTaskAttachmentCache } from '../../pwa/taskAttachmentCache'
import { TaskTimeline } from './TaskTimeline'

const localImageMessage: TaskTimelineItem = {
  id: 'photo-message', kind: 'user', author: 'Анна', text: 'Готово',
  created_at: '2026-09-15T10:00:00Z', sync_state: 'saved',
  attachments: [{
    id: 'photo', name: 'repair.jpg', mimetype: 'image/jpeg',
    url: '/api/tracker/issues/ROBOPARK-1/attachments/photo/content',
  }],
}

beforeEach(async () => {
  class TestURL extends URL {}
  TestURL.createObjectURL = vi.fn(() => 'blob:repair-preview')
  TestURL.revokeObjectURL = vi.fn()
  vi.stubGlobal('URL', TestURL)
  await activateTaskAttachmentCache(7)
})

afterEach(async () => {
  await clearTaskAttachmentCache()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it('renders one chronological chat and keeps attachments inside their message', () => {
  render(<TaskTimeline items={[
    { id: 'later', kind: 'system', author: 'СУРП', text: 'Передано на проверку', created_at: '2026-09-15T10:02:00Z', sync_state: 'pending', attachments: [] },
    { id: 'first', kind: 'user', author: 'Анна', text: 'Заменила датчик', created_at: '2026-09-15T10:00:00Z', sync_state: 'synced', attachments: [{ id: 'manual', name: 'manual.pdf', url: 'https://tracker.example/manual.pdf', mimetype: 'application/pdf' }] },
    { id: 'middle', kind: 'tracker', author: 'Бот', text: 'Принято', created_at: '2026-09-15T10:01:00Z', sync_state: 'saved', attachments: [] },
  ]} />)

  const messages = screen.getAllByRole('listitem')
  expect(messages.map(item => item.textContent)).toEqual(expect.arrayContaining([]))
  expect(messages[0]).toHaveTextContent('Заменила датчик')
  expect(messages[1]).toHaveTextContent('Принято')
  expect(messages[2]).toHaveTextContent('Передано на проверку')
  expect(within(messages[0]!).getByRole('link', { name: 'manual.pdf' })).toHaveAttribute('href', 'https://tracker.example/manual.pdf')
  expect(screen.queryByRole('heading', { name: 'История действий' })).not.toBeInTheDocument()
})

it('explains why an old-cycle draft will not be resent into reopened Tracker work', () => {
  render(<TaskTimeline items={[{
    id: 'old-cycle', kind: 'user', author: 'Анна', text: 'Фото и описание ремонта',
    created_at: '2026-09-15T10:00:00Z', sync_state: 'needs_attention',
    delivery_note: 'previous_cycle_not_sent', attachments: [],
  }]} />)

  expect(screen.getByText('Фото и описание ремонта')).toBeVisible()
  expect(screen.getByText(/предыдущего цикла.*не отправлено в Tracker/i)).toBeVisible()
})

it('renders a local image thumbnail and revokes its object URL', async () => {
  const fetchAttachment = vi.fn(async () => new Response('photo', {
    headers: { 'Content-Type': 'image/jpeg' },
  }))
  vi.stubGlobal('fetch', fetchAttachment)

  const view = render(<TaskTimeline items={[localImageMessage]} />)

  const image = await screen.findByRole('img', { name: 'repair.jpg' })
  expect(image).toHaveAttribute('loading', 'lazy')
  expect(image).toHaveAttribute('src', 'blob:repair-preview')
  expect(image.closest('a')).toHaveAttribute('href', 'blob:repair-preview')
  expect(fetchAttachment).toHaveBeenCalledWith(
    '/api/tracker/issues/ROBOPARK-1/attachments/photo/content',
    expect.objectContaining({ credentials: 'include', signal: expect.any(AbortSignal) }),
  )

  view.unmount()
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:repair-preview')
})

it('keeps a local non-image attachment as an authenticated content link', () => {
  render(<TaskTimeline items={[{
    ...localImageMessage,
    attachments: [{
      id: 'manual', name: 'manual.pdf', mimetype: 'application/pdf',
      url: '/api/tracker/issues/ROBOPARK-1/attachments/manual/content',
    }],
  }]} />)

  expect(screen.getByRole('link', { name: 'manual.pdf' }))
    .toHaveAttribute('href', '/api/tracker/issues/ROBOPARK-1/attachments/manual/content')
  expect(screen.queryByRole('img')).not.toBeInTheDocument()
})

it('drops an owned preview when reconciliation replaces the attachment content', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('photo', {
    headers: { 'Content-Type': 'image/jpeg' },
  })))
  const view = render(<TaskTimeline items={[localImageMessage]} />)
  expect(await screen.findByRole('img', { name: 'repair.jpg' })).toBeInTheDocument()

  view.rerender(<TaskTimeline items={[{
    ...localImageMessage,
    attachments: [{
      id: 'photo', name: 'repair.pdf', mimetype: 'application/pdf',
      url: '/api/tracker/issues/ROBOPARK-1/attachments/photo/content',
    }],
  }]} />)

  expect(screen.queryByRole('img')).not.toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'repair.pdf' })).toBeInTheDocument()
})
