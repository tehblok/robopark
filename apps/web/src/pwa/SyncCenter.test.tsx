import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SyncContextProvider, type SyncContextValue } from './SyncProvider'
import { SyncCenter } from './SyncCenter'
import type { OfflineAction, OfflineMedia } from './offlineTypes'

function syncValue(overrides: Partial<SyncContextValue> = {}): SyncContextValue {
  return {
    state: { status: 'attention', pending: 2, conflicts: 1 },
    enqueueAction: vi.fn(), enqueueMedia: vi.fn(),
    syncNow: vi.fn(async () => true), cancelAction: vi.fn(async () => undefined),
    resolveConflict: vi.fn(async () => undefined), findAction: vi.fn(async () => undefined),
    subscribeAction: vi.fn(() => () => undefined), ...overrides,
  }
}

describe('SyncCenter', () => {
  it('keeps the indicator offline until a restored session is confirmed by the server', () => {
    const { container, rerender } = render(<SyncContextProvider value={syncValue({ state: { status: 'idle', pending: 0, conflicts: 0 } })}><SyncCenter offlineSession updateReady /></SyncContextProvider>)
    expect(container.querySelector('.rp-sync-center__dot')).toHaveClass('is-offline')
    fireEvent.click(screen.getByRole('button', { name: /Открыть центр синхронизации/ }))
    expect(screen.queryByRole('button', { name: 'Установить обновление' })).not.toBeInTheDocument()
    rerender(<SyncContextProvider value={syncValue({ state: { status: 'idle', pending: 0, conflicts: 0 } })}><SyncCenter offlineSession={false} /></SyncContextProvider>)
    expect(container.querySelector('.rp-sync-center__dot')).toHaveClass('is-idle')
  })
  it('uses only the top indicator without a visible synchronization caption', () => {
    render(<SyncContextProvider value={syncValue({ state: { status: 'syncing', pending: 1, conflicts: 0 } })}><SyncCenter /></SyncContextProvider>)
    const trigger = screen.getByRole('button', { name: /Открыть центр синхронизации/ })
    expect(trigger).not.toHaveTextContent('Отправляем')
    expect(trigger.querySelector('.rp-sync-center__dot')).toBeInTheDocument()
  })
  it('shows the actual scoped action and failed photo instead of an empty queue', async () => {
    const action: OfflineAction = {
      id: 'review-1', deviceId: 'phone', resourceType: 'tracker_issue', resourceId: 'ROBOPARK-42',
      action: 'submit_review', idempotencyKey: 'review-1-key', baseRevision: null,
      dependencies: ['photo-1'], payload: { media_id: 'photo-1' }, state: 'ready',
      attempts: 0, createdAt: 1, updatedAt: 2,
    }
    const media: OfflineMedia = {
      id: 'photo-1', actionId: action.id, issueKey: 'ROBOPARK-42', name: 'robot.jpg',
      blob: new Blob(['photo']), mimeType: 'image/jpeg', sha256: 'a', sizeBytes: 5,
      state: 'attention', attempts: 1, createdAt: 1, updatedAt: 2,
    }
    const value = syncValue({ listActions: vi.fn(async () => [action]), listMedia: vi.fn(async () => [media]) } as Partial<SyncContextValue>)
    render(<SyncContextProvider value={value}><SyncCenter /></SyncContextProvider>)
    fireEvent.click(screen.getByRole('button', { name: /Открыть центр синхронизации/ }))

    expect(await screen.findByText('Передать на проверку · ROBOPARK-42')).toBeVisible()
    expect(screen.getByText('Фото · ROBOPARK-42')).toBeVisible()
    expect(screen.getByText('Требует внимания')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Отменить «Передать на проверку · ROBOPARK-42»' }))
    expect(value.cancelAction).toHaveBeenCalledWith('review-1')
  })

  it.each(['offline_dependency_missing', 'dependency_missing', 'dependency_failed'])(
    'explains the %s prerequisite failure and offers cancellation without exposing local identifiers', async code => {
    const action: OfflineAction = {
      id: 'review-private-id', deviceId: 'phone', resourceType: 'tracker_issue', resourceId: 'ROBOPARK-42',
      action: 'submit_review', idempotencyKey: 'review-private-key', baseRevision: null,
      dependencies: ['private-comment-id'], payload: { defect_code: 'code' },
      result: { code }, state: 'attention',
      attempts: 0, createdAt: 1, updatedAt: 2,
    }
    const value = syncValue({ state: { status: 'attention', pending: 1, conflicts: 1 },
      listActions: async () => [action], listMedia: async () => [] })
    render(<SyncContextProvider value={value}><SyncCenter /></SyncContextProvider>)
    fireEvent.click(screen.getByRole('button', { name: /Открыть центр синхронизации/ }))

    expect(await screen.findByText('Передать на проверку · ROBOPARK-42')).toBeVisible()
    expect(screen.getByText('Обязательная предыдущая запись недоступна. Отмените действие и создайте его заново.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Отменить «Передать на проверку · ROBOPARK-42»' })).toBeVisible()
    expect(screen.queryByText('private-comment-id')).not.toBeInTheDocument()
    },
  )

  it('explains an access rejection without displaying the server code', async () => {
    const action: OfflineAction = {
      id: 'private-action-id', deviceId: 'phone', resourceType: 'tracker_issue', resourceId: 'ROBOPARK-42',
      action: 'comment', idempotencyKey: 'private-key', baseRevision: null,
      dependencies: [], payload: { text: 'private' }, result: { code: 'park_forbidden' }, state: 'attention',
      attempts: 0, createdAt: 1, updatedAt: 2,
    }
    const value = syncValue({ listActions: async () => [action], listMedia: async () => [] })
    render(<SyncContextProvider value={value}><SyncCenter /></SyncContextProvider>)
    fireEvent.click(screen.getByRole('button', { name: /Открыть центр синхронизации/ }))

    expect(await screen.findByText('Комментарий · ROBOPARK-42')).toBeVisible()
    expect(screen.getByText('Не завершено: 2')).toBeVisible()
    expect(screen.getByText('Доступ к парку изменился. Отмените действие и проверьте доступ перед повтором.')).toBeVisible()
    expect(screen.getByText('Действие не будет отправлено повторно. Выберите решение в строке выше.')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Повторить отправку' })).not.toBeInTheDocument()
    expect(screen.queryByText('park_forbidden')).not.toBeInTheDocument()
    expect(screen.queryByText('private-action-id')).not.toBeInTheDocument()
  })

  it('hides an old account queue before reading the next account scope', async () => {
    const action: OfflineAction = {
      id: 'old', deviceId: 'phone', resourceType: 'tracker_issue', resourceId: 'OLD-1',
      action: 'comment', idempotencyKey: 'old-key', baseRevision: null, dependencies: [],
      payload: { text: 'private' }, state: 'ready', attempts: 0, createdAt: 1, updatedAt: 1,
    }
    const first = syncValue({ scopeKey: 'account-a', listActions: async () => [action], listMedia: async () => [] })
    const second = syncValue({ scopeKey: 'account-b', listActions: async () => [], listMedia: async () => [] })
    const view = render(<SyncContextProvider value={first}><SyncCenter /></SyncContextProvider>)
    fireEvent.click(screen.getByRole('button', { name: /Открыть центр синхронизации/ }))
    expect(await screen.findByText('Комментарий · OLD-1')).toBeVisible()

    view.rerender(<SyncContextProvider value={second}><SyncCenter /></SyncContextProvider>)
    expect(screen.queryByText('Комментарий · OLD-1')).not.toBeInTheDocument()
  })

  it('names a queued schedule action without exposing its internal identifier', async () => {
    const action: OfflineAction = {
      id: 'schedule-1', deviceId: 'phone', resourceType: 'schedule_entry',
      resourceId: '0a416ff5-3149-4a7c-93eb-f1814f513028', action: 'schedule_create',
      idempotencyKey: 'schedule-1-key', baseRevision: null, dependencies: [],
      payload: { park_id: 7 }, state: 'ready', attempts: 0, createdAt: 1, updatedAt: 1,
    }
    const value = syncValue({ listActions: async () => [action], listMedia: async () => [] })
    render(<SyncContextProvider value={value}><SyncCenter /></SyncContextProvider>)
    fireEvent.click(screen.getByRole('button', { name: /Открыть центр синхронизации/ }))
    expect(await screen.findByText('Создать период · График')).toBeVisible()
    expect(screen.queryByText(action.resourceId)).not.toBeInTheDocument()
  })



  it.each([
    ['idle', { status: 'idle' as const, pending: 0, conflicts: 0 }, 'Синхронизация выполняется автоматически', 'is-idle'],
    ['syncing', { status: 'syncing' as const, pending: 0, conflicts: 0 }, 'Автосинхронизация: отправляем', 'is-syncing'],
    ['offline', { status: 'offline' as const, pending: 0, conflicts: 0 }, 'Автосинхронизация: без сети', 'is-offline'],
    ['pending', { status: 'idle' as const, pending: 2, conflicts: 0 }, 'Автосинхронизация: ожидает отправки', 'is-pending'],
    ['attention', { status: 'attention' as const, pending: 0, conflicts: 0 }, 'Автосинхронизация: нужно внимание', 'is-attention'],
  ])('keeps the compact %s state visible and exposes it in the trigger name', (_name, state, label, stateClass) => {
    const { container } = render(<SyncContextProvider value={syncValue({ state })}><SyncCenter /></SyncContextProvider>)
    expect(screen.getByRole('button', { name: new RegExp(label) })).toBeInTheDocument()
    expect(container.querySelector('.rp-sync-center__dot')).toHaveClass(stateClass)
  })

  it('does not display a permanent success badge when the queue is empty', () => {
    render(<SyncContextProvider value={syncValue({ state: { status: 'idle', pending: 0, conflicts: 0 } })}><SyncCenter /></SyncContextProvider>)
    expect(screen.queryByText('Всё отправлено')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Открыть центр синхронизации/ })).toHaveTextContent('')
  })
  it('shows the offline indicator even when no actions are pending', () => {
    const online = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true)
    const { container } = render(<SyncContextProvider value={syncValue({ state: { status: 'idle', pending: 0, conflicts: 0 } })}><SyncCenter /></SyncContextProvider>)
    online.mockReturnValue(false)
    fireEvent(window, new Event('offline'))
    expect(container.querySelector('.rp-sync-center__dot')).toHaveClass('is-offline')
    expect(screen.getByRole('button', { name: /без сети/ })).toBeInTheDocument()
    online.mockRestore()
  })
  it('uses an indicator and accessible explanation without a visible sync caption', () => {
    const { rerender } = render(<SyncContextProvider value={syncValue({ state: { status: 'idle', pending: 0, conflicts: 0 } })}><SyncCenter /></SyncContextProvider>)
    const visibleLabel = () => screen.getByRole('button', { name: /Открыть центр синхронизации/ })
      .querySelector('span:not(.rp-sync-center__dot)')?.textContent

    expect(visibleLabel()).toBeUndefined()
    expect(screen.getByRole('button', { name: /Синхронизация выполняется автоматически/ })).toBeInTheDocument()
    rerender(<SyncContextProvider value={syncValue({ state: { status: 'attention', pending: 2, conflicts: 1 } })}><SyncCenter /></SyncContextProvider>)
    expect(visibleLabel()).toBeUndefined()
    expect(screen.getByRole('button', { name: /Автосинхронизация: нужно внимание/ })).toHaveTextContent('2')
    expect(screen.getByRole('button', { name: /Автосинхронизация: нужно внимание/ })).toBeInTheDocument()
  })
  it('uses gigabytes for a large device quota so the phone summary stays compact', async () => {
    render(<SyncContextProvider value={syncValue()}><SyncCenter estimateStorage={async () => ({ usage: 0, quota: 5 * 1024 ** 3 })} /></SyncContextProvider>)
    fireEvent.click(screen.getByRole('button', { name: /Открыть центр синхронизации/ }))
    expect(await screen.findByText('На устройстве: 0,0 МБ из 5,0 ГБ')).toBeInTheDocument()
  })
  it('does not present an unavailable storage estimate as zero usage and quota', async () => {
    render(<SyncContextProvider value={syncValue()}><SyncCenter estimateStorage={async () => ({})} /></SyncContextProvider>)
    fireEvent.click(screen.getByRole('button', { name: /Открыть центр синхронизации/ }))
    expect(await screen.findByText('Объём на устройстве не измерен')).toBeInTheDocument()
    expect(screen.queryByText('На устройстве: 0,0 МБ из 0,0 МБ')).not.toBeInTheDocument()
  })


  it('summarizes queue, conflicts and local storage without technical language', async () => {
    const value = syncValue()
    render(<SyncContextProvider value={value}><SyncCenter
      estimateStorage={async () => ({ usage: 2 * 1024 * 1024, quota: 10 * 1024 * 1024 })}
      queue={[{ id: 'comment-1', label: 'Комментарий к SDCFLEETOPS-1', state: 'pending' }]}
    /></SyncContextProvider>)

    expect(screen.getByRole('button', { name: /Открыть центр синхронизации/ })).toHaveTextContent('2')
    fireEvent.click(screen.getByRole('button', { name: /Открыть центр синхронизации/ }))
    expect(screen.getByText('Не завершено: 2')).toBeInTheDocument()
    expect(screen.getByText('Требуют решения: 1')).toBeInTheDocument()
    expect(screen.getByText('Комментарий к SDCFLEETOPS-1')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('На устройстве: 2,0 МБ из 10,0 МБ')).toBeInTheDocument())

    expect(screen.getByText(/После исправления причины/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Повторить отправку' }))
    expect(value.syncNow).toHaveBeenCalledWith('manual')
    fireEvent.click(screen.getByRole('button', { name: 'Отменить «Комментарий к SDCFLEETOPS-1»' }))
    expect(value.cancelAction).toHaveBeenCalledWith('comment-1')
  })

  it('does not offer an update while an action is pending', () => {
    const activateUpdate = vi.fn(() => false)
    render(<SyncContextProvider value={syncValue()}><SyncCenter updateReady activateUpdate={activateUpdate} /></SyncContextProvider>)
    fireEvent.click(screen.getByRole('button', { name: /Открыть центр синхронизации/ }))
    expect(screen.getByText('Обновление будет доступно после отправки очереди.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Установить обновление' })).not.toBeInTheDocument()
  })

  it('does not offer an update while a failed fallback action needs attention', () => {
    render(<SyncContextProvider value={syncValue({ state: { status: 'attention', pending: 0, conflicts: 1 } })}><SyncCenter updateReady /></SyncContextProvider>)
    fireEvent.click(screen.getByRole('button', { name: /Открыть центр синхронизации/ }))
    expect(screen.queryByRole('button', { name: 'Установить обновление' })).not.toBeInTheDocument()
  })
})
