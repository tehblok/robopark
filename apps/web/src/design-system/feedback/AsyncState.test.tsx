import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ErrorState, EmptyState, LoadingState, StaleBadge } from './AsyncState'

describe('async states', () => {
  it('gives loading state a status name and busy state', () => {
    render(<LoadingState label="Загружаем парк" variant="panel" />)
    expect(screen.getByRole('status', { name: 'Загружаем парк' })).toHaveAttribute('aria-busy', 'true')
  })

  it('announces errors and retries through the action button', async () => {
    const retry = vi.fn()
    const user = userEvent.setup()
    render(<ErrorState title="Не удалось загрузить" description="Проверьте сеть" onRetry={retry} />)

    expect(screen.getByRole('alert')).toHaveTextContent('Проверьте сеть')
    await user.click(screen.getByRole('button', { name: 'Повторить' }))
    expect(retry).toHaveBeenCalledOnce()
  })

  it('renders an explicit empty state with its optional action', () => {
    render(<EmptyState title="Нет роботов" description="Добавьте робота в парк" action={<button>Добавить</button>} />)
    expect(screen.getByRole('heading', { name: 'Нет роботов' })).toBeVisible()
    expect(screen.getByText('Добавьте робота в парк')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Добавить' })).toBeVisible()
  })

  it('renders stale freshness text, icon, and a localized valid time', () => {
    render(<StaleBadge state="stale" updatedAt="2026-09-02T08:00:00Z" />)
    expect(screen.getByText(/Данные устарели/)).toBeVisible()
    const expectedTime = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' }).format(new Date('2026-09-02T08:00:00Z'))
    expect(screen.getByText(new RegExp(expectedTime))).toBeVisible()
    expect(screen.getByText(/Данные устарели/).closest('.rp-status-badge')?.querySelector('svg')).toBeTruthy()
  })

  it('keeps canonical freshness text when a supplementary label is provided', () => {
    render(<StaleBadge state="stale" label="Последний интервал" updatedAt="2026-09-02T08:00:00Z" />)
    const badge = screen.getByText(/Последний интервал/).closest('.rp-status-badge')
    const expectedTime = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' }).format(new Date('2026-09-02T08:00:00Z'))
    expect(badge).toHaveTextContent('Последний интервал')
    expect(badge).toHaveTextContent('Данные устарели')
    expect(badge).toHaveTextContent(expectedTime)
  })

  it('omits time for invalid freshness timestamps and supports every state', () => {
    for (const state of ['live', 'fresh', 'stale', 'offline'] as const) {
      const { unmount } = render(<StaleBadge state={state} updatedAt="not-a-date" />)
      const expectedText = state === 'live' ? 'Данные актуальны' : state === 'fresh' ? 'Данные свежие' : state === 'stale' ? 'Данные устарели' : 'Нет связи с источником'
      expect(screen.getByText(expectedText).closest('.rp-status-badge')?.querySelector('svg')).toBeTruthy()
      expect(screen.getByText(expectedText)).toBeVisible()
      unmount()
    }
  })
})
