import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ApiError } from '../../api'
import { TaskCollaboration } from './TaskCollaboration'
import { collaborationClient } from './collaborationClient'

const saved = { revision: 1, done: 'Мотор', remaining: 'Тест', obstacles: '', author: 'alice', updated_at: '2026-09-06T10:00:00Z' }
beforeEach(() => localStorage.clear())
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers() })

it('uses the lifecycle handoff form instead of saving legacy notes', async () => {
  const onHandoff = vi.fn(async () => undefined)
  const save = vi.spyOn(collaborationClient, 'save')
  render(<TaskCollaboration issueKey="RP-1" owner="alice" active canWrite lifecycle onHandoff={onHandoff} />)
  fireEvent.change(screen.getByLabelText('Логин сменщика'), { target: { value: 'bob' } })
  fireEvent.change(screen.getByLabelText('Причина передачи'), { target: { value: 'Смена закончилась' } })
  fireEvent.change(screen.getByLabelText('Сделано'), { target: { value: 'Заменён мотор' } })
  fireEvent.change(screen.getByLabelText('Осталось'), { target: { value: 'Проверить' } })
  fireEvent.click(screen.getByRole('button', { name: 'Передать смену' }))
  await waitFor(() => expect(onHandoff).toHaveBeenCalledWith({ assignee: 'bob', reason: 'Смена закончилась', done: 'Заменён мотор', remaining: 'Проверить', obstacles: '' }))
  expect(save).not.toHaveBeenCalled()
})

it('uses the shared primary action inside the open handoff disclosure', () => {
  render(<TaskCollaboration issueKey="RP-1" owner="alice" active canWrite lifecycle onHandoff={async () => undefined} />)

  expect(screen.getByRole('button', { name: 'Передать смену' })).toHaveClass('rp-disclosure-primary-action')
})

it('loads handoff only when opened and preserves edited text while showing a conflicting revision', async () => {
  const get = vi.spyOn(collaborationClient, 'handoff').mockResolvedValue(saved)
  vi.spyOn(collaborationClient, 'save').mockRejectedValue(new ApiError(409, 'tracker_handoff_conflict'))
  render(<TaskCollaboration issueKey="RP-1" owner="alice" active={false} canWrite />)
  expect(get).not.toHaveBeenCalled()
  fireEvent.click(screen.getByText('Передача смены'))
  await screen.findByDisplayValue('Мотор')
  fireEvent.change(screen.getByLabelText('Сделано'), { target: { value: 'Мой текст' } })
  get.mockResolvedValue({ ...saved, revision: 2, done: 'Текст коллеги', author: 'bob' })
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить передачу смены' }))
  await screen.findByText('Сделано: Текст коллеги')
  expect(screen.getByLabelText('Сделано')).toHaveValue('Мой текст')
  expect(screen.getByRole('button', { name: 'Сохранить передачу смены' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Продолжить с моим текстом' }))
  expect(screen.getByRole('button', { name: 'Сохранить передачу смены' })).toBeEnabled()
})

it('shows one retryable error instead of an endless loading state when handoff loading fails', async () => {
  const get = vi.spyOn(collaborationClient, 'handoff')
    .mockRejectedValueOnce(new Error('offline'))
    .mockResolvedValueOnce(saved)
  render(<TaskCollaboration issueKey="RP-1" owner="alice" active={false} canWrite />)

  fireEvent.click(screen.getByText('Передача смены'))
  expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось загрузить передачу смены.')
  expect(screen.queryByText('Загружаем передачу смены…')).not.toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: 'Повторить загрузку' }))
  expect(await screen.findByDisplayValue('Мотор')).toBeVisible()
  expect(get).toHaveBeenCalledTimes(2)
})

it('stops presence when the task or browser tab is hidden', async () => {
  vi.useFakeTimers()
  vi.spyOn(Math, 'random').mockReturnValue(0)
  const ping = vi.spyOn(collaborationClient, 'presence').mockResolvedValue({ people: [{ username: 'bob' }] })
  const view = render(<TaskCollaboration issueKey="RP-1" owner="alice" active canWrite />)
  await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
  expect(ping).toHaveBeenCalledTimes(1)
  expect(screen.getByRole('status')).toHaveTextContent('bob')
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
  fireEvent(document, new Event('visibilitychange'))
  await act(async () => { await vi.advanceTimersByTimeAsync(100_000) })
  expect(ping).toHaveBeenCalledTimes(1)
  view.rerender(<TaskCollaboration issueKey="RP-1" owner="alice" active={false} canWrite />)
  await act(async () => { await vi.advanceTimersByTimeAsync(100_000) })
  expect(ping).toHaveBeenCalledTimes(1)
})

it('restores drafts after reopen and isolates a different account', async () => {
  vi.spyOn(collaborationClient, 'handoff').mockResolvedValue(saved)
  const view = render(<TaskCollaboration issueKey="RP-1" owner="alice" active={false} canWrite />)
  fireEvent.click(screen.getByText('Передача смены'))
  await screen.findByDisplayValue('Мотор')
  fireEvent.change(screen.getByLabelText('Осталось'), { target: { value: 'Мой черновик' } })
  view.unmount()
  const next = render(<TaskCollaboration issueKey="RP-1" owner="alice" active={false} canWrite />)
  fireEvent.click(screen.getByText('Передача смены'))
  await screen.findByDisplayValue('Мой черновик')
  next.rerender(<TaskCollaboration issueKey="RP-1" owner="bob" active={false} canWrite />)
  fireEvent.click(screen.getByText('Передача смены'))
  await waitFor(() => expect(screen.getByLabelText('Осталось')).toHaveValue('Тест'))
})


it.each([401, 403])('hides loaded handoff and write controls on save %s without a parent callback', async status => {
  vi.spyOn(collaborationClient, 'handoff').mockResolvedValue(saved)
  vi.spyOn(collaborationClient, 'save').mockRejectedValue(new ApiError(status))
  render(<TaskCollaboration issueKey="RP-1" owner="alice" active={false} canWrite />)
  fireEvent.click(screen.getByText('Передача смены'))
  await screen.findByDisplayValue('Мотор')
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить передачу смены' }))
  await waitFor(() => expect(screen.queryAllByRole('textbox')).toHaveLength(0))
  expect(screen.queryByText(/alice/)).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Сохранить передачу смены' })).not.toBeInTheDocument()
})

it('hides both presence and loaded handoff on a polling denial without a parent callback', async () => {
  vi.useFakeTimers()
  vi.spyOn(Math, 'random').mockReturnValue(0)
  vi.spyOn(collaborationClient, 'presence').mockResolvedValueOnce({ people: [{ username: 'bob' }] }).mockRejectedValue(new ApiError(403))
  vi.spyOn(collaborationClient, 'handoff').mockResolvedValue(saved)
  render(<TaskCollaboration issueKey="RP-1" owner="alice" active canWrite />)
  fireEvent.click(screen.getByText('Передача смены'))
  await act(async () => { await vi.advanceTimersByTimeAsync(1) })
  expect(screen.getByDisplayValue('Мотор')).toBeInTheDocument()
  expect(screen.getByRole('status')).toHaveTextContent('bob')
  await act(async () => { await vi.advanceTimersByTimeAsync(35_000) })
  expect(screen.queryAllByRole('textbox')).toHaveLength(0)
  expect(screen.queryByText(/Сейчас в задаче/)).not.toBeInTheDocument()
})


it('clears a successfully accepted handoff so a second activation cannot repeat it', async () => {
  const onHandoff = vi.fn(async () => undefined)
  render(<TaskCollaboration issueKey="RP-1" owner="alice" active canWrite lifecycle onHandoff={onHandoff} />)
  fireEvent.change(screen.getByLabelText('Логин сменщика'), { target: { value: 'bob' } })
  fireEvent.change(screen.getByLabelText('Причина передачи'), { target: { value: 'Смена' } })
  const submit = screen.getByRole('button', { name: 'Передать смену' })
  fireEvent.click(submit)
  await waitFor(() => expect(screen.getByLabelText('Логин сменщика')).toHaveValue(''))
  fireEvent.click(submit)
  expect(onHandoff).toHaveBeenCalledTimes(1)
})

it('guards two handoff events before React commits the busy state', async () => {
  let finish!: () => void
  const onHandoff = vi.fn(() => new Promise<void>(resolve => { finish = resolve }))
  render(<TaskCollaboration issueKey="RP-1" owner="alice" active canWrite lifecycle onHandoff={onHandoff} />)
  fireEvent.change(screen.getByLabelText('Логин сменщика'), { target: { value: 'bob' } })
  fireEvent.change(screen.getByLabelText('Причина передачи'), { target: { value: 'Смена' } })
  const submit = screen.getByRole('button', { name: 'Передать смену' })
  act(() => {
    submit.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    submit.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })
  expect(onHandoff).toHaveBeenCalledTimes(1)
  await act(async () => finish())
})

it('preserves edits made while a handoff is being accepted and keeps failed input retryable', async () => {
  let finish!: () => void
  const onHandoff = vi.fn(() => new Promise<void>(resolve => { finish = resolve }))
  render(<TaskCollaboration issueKey="RP-1" owner="alice" active canWrite lifecycle onHandoff={onHandoff} />)
  fireEvent.change(screen.getByLabelText('Логин сменщика'), { target: { value: 'bob' } })
  fireEvent.change(screen.getByLabelText('Причина передачи'), { target: { value: 'Смена' } })
  fireEvent.click(screen.getByRole('button', { name: 'Передать смену' }))
  fireEvent.change(screen.getByLabelText('Причина передачи'), { target: { value: 'Новый текст' } })
  await act(async () => finish())
  expect(screen.getByLabelText('Причина передачи')).toHaveValue('Новый текст')
  onHandoff.mockRejectedValueOnce(new Error('offline'))
  fireEvent.click(screen.getByRole('button', { name: 'Передать смену' }))
  await screen.findByRole('alert')
  expect(screen.getByLabelText('Причина передачи')).toHaveValue('Новый текст')
  expect(screen.getByRole('button', { name: 'Передать смену' })).toBeEnabled()
})

it('starts a separate lifecycle handoff draft for each owner and issue', () => {
  const onHandoff = vi.fn(async () => undefined)
  const view = render(<TaskCollaboration issueKey="RP-1" owner="alice" active canWrite lifecycle onHandoff={onHandoff} />)
  fireEvent.change(screen.getByLabelText('Логин сменщика'), { target: { value: 'bob' } })
  view.rerender(<TaskCollaboration issueKey="RP-2" owner="alice" active canWrite lifecycle onHandoff={onHandoff} />)
  expect(screen.getByLabelText('Логин сменщика')).toHaveValue('')
  fireEvent.change(screen.getByLabelText('Логин сменщика'), { target: { value: 'carol' } })
  view.rerender(<TaskCollaboration issueKey="RP-2" owner="dave" active canWrite lifecycle onHandoff={onHandoff} />)
  expect(screen.getByLabelText('Логин сменщика')).toHaveValue('')
})
