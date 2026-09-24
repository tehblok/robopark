import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SyncContextProvider, type SyncContextValue } from './SyncProvider'
import { SyncCenter } from './SyncCenter'

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
  it('does not display a permanent success badge when the queue is empty', () => {
    render(<SyncContextProvider value={syncValue({ state: { status: 'idle', pending: 0, conflicts: 0 } })}><SyncCenter /></SyncContextProvider>)
    expect(screen.queryByText('Всё отправлено')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Открыть центр синхронизации' })).toHaveTextContent('Синхронизация')
  })
  it('summarizes queue, conflicts and local storage without technical language', async () => {
    const value = syncValue()
    render(<SyncContextProvider value={value}><SyncCenter
      estimateStorage={async () => ({ usage: 2 * 1024 * 1024, quota: 10 * 1024 * 1024 })}
      queue={[{ id: 'comment-1', label: 'Комментарий к SDCFLEETOPS-1', state: 'pending' }]}
    /></SyncContextProvider>)

    expect(screen.getByRole('button', { name: 'Открыть центр синхронизации' })).toHaveTextContent('2')
    fireEvent.click(screen.getByRole('button', { name: 'Открыть центр синхронизации' }))
    expect(screen.getByText('В очереди: 2')).toBeInTheDocument()
    expect(screen.getByText('Требуют решения: 1')).toBeInTheDocument()
    expect(screen.getByText('Комментарий к SDCFLEETOPS-1')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('На устройстве: 2,0 МБ из 10,0 МБ')).toBeInTheDocument())

    expect(screen.getByText('Отправка выполняется автоматически.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Повторить отправку' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Отменить «Комментарий к SDCFLEETOPS-1»' }))
    expect(value.cancelAction).toHaveBeenCalledWith('comment-1')
  })

  it('does not offer an update while an action is pending', () => {
    const activateUpdate = vi.fn(() => false)
    render(<SyncContextProvider value={syncValue()}><SyncCenter updateReady activateUpdate={activateUpdate} /></SyncContextProvider>)
    fireEvent.click(screen.getByRole('button', { name: 'Открыть центр синхронизации' }))
    expect(screen.getByText('Обновление будет доступно после отправки очереди.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Установить обновление' })).not.toBeInTheDocument()
  })
})
