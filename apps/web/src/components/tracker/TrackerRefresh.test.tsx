import { webcrypto } from 'node:crypto'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, type TrackerIssueDetail } from '../../api'
import { AuthContext } from '../../auth-context'
import { resourceStore } from '../../lib/resource'
import { ru } from '../../i18n/ru'
import { TrackerWorkspace } from './TrackerWorkspace'
import { IssueDrawer } from './IssueDrawer'

vi.mock('./IssueFilters', () => ({ IssueFilters: () => null }))
vi.mock('./IssueDetailPanel', () => ({ IssueDetailPanel: ({ issue }: { issue: TrackerIssueDetail | null }) => <output>{issue?.summary}</output> }))
vi.mock('./IssueActionsPanel', () => ({ IssueActionsPanel: ({ onAssign }: { onAssign: (name: string) => Promise<void> }) => <button onClick={() => void onAssign('me')}>Назначить</button> }))
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done }); return { promise, resolve } }
function issue(id: number, summary = `Задача ${id}`) { return { key: `RP-${id}`, summary, status: 'Открыта' } as TrackerIssueDetail }
function page(offset: number, total = 120, prefix = 'Задача') { return { items: Array.from({ length: Math.min(50, total - offset) }, (_, index) => issue(offset + index + 1, `${prefix} ${offset + index + 1}`)), total, offset, limit: 50, has_more: offset + 50 < total } }
function tree(drawer = false) { return <AuthContext.Provider value={{ user: null, loading: false, login: vi.fn(), logout: vi.fn(), refreshUser: vi.fn() }}>{drawer ? <IssueDrawer issueKey="RP-1" canWrite onClose={vi.fn()} /> : <TrackerWorkspace allowUntagged canWrite />}</AuthContext.Provider> }
beforeEach(() => {
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true })))
  vi.spyOn(api, 'trackerIssue').mockResolvedValue(issue(1))
  vi.spyOn(api, 'trackerComments').mockResolvedValue([])
  vi.spyOn(api, 'trackerTransitions').mockResolvedValue([])
  vi.spyOn(api, 'trackerAssign').mockResolvedValue({ key: 'RP-1', action: 'assign', status: 'done', actor: 'me', performed_at: '2026-09-06T00:00:00Z' })
})
afterEach(() => { resourceStore.clearAll(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('refreshes every loaded page without collapsing pagination to the first 50', async () => {
  const list = vi.spyOn(api, 'trackerIssues').mockImplementation(async query => page(query.offset ?? 0))
  render(tree())
  await screen.findByRole('button', { name: 'Открыть задачу RP-50: Задача 50' })
  fireEvent.click(screen.getByRole('button', { name: ru.tracker.showMore }))
  await screen.findByRole('button', { name: 'Открыть задачу RP-100: Задача 100' })
  list.mockImplementation(async query => page(query.offset ?? 0, 120, 'Свежая'))
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 30_001)
  fireEvent.focus(window)
  await screen.findByRole('button', { name: 'Открыть задачу RP-100: Свежая 100' })
  expect(list.mock.calls.map(([query]) => query.offset)).toEqual([0, 50, 0, 50])
  expect(screen.getAllByRole('button', { name: /Открыть задачу/ })).toHaveLength(100)
})

it('does not start background pagination while the next page remains pending', async () => {
  const pending = deferred<ReturnType<typeof page>>()
  const list = vi.spyOn(api, 'trackerIssues').mockResolvedValueOnce(page(0)).mockReturnValueOnce(pending.promise)
  render(tree())
  await screen.findByRole('button', { name: 'Открыть задачу RP-50: Задача 50' })
  fireEvent.click(screen.getByRole('button', { name: ru.tracker.showMore }))
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 30_001)
  fireEvent.focus(window)
  expect(list).toHaveBeenCalledTimes(2)
  await act(async () => pending.resolve(page(50)))
  expect(screen.getAllByRole('button', { name: /Открыть задачу/ })).toHaveLength(100)
})

it.each([false, true])('mutation reload retires a pre-mutation detail poll (drawer=%s)', async drawer => {
  const pending = deferred<TrackerIssueDetail>()
  vi.spyOn(api, 'trackerIssues').mockResolvedValue(page(0))
  const detail = vi.mocked(api.trackerIssue).mockResolvedValueOnce(issue(1)).mockReturnValueOnce(pending.promise).mockResolvedValue(issue(1, 'Изменение сохранено'))
  render(tree(drawer))
  if (!drawer) fireEvent.click(await screen.findByRole('button', { name: 'Открыть задачу RP-1: Задача 1' }))
  await screen.findByRole('button', { name: 'Назначить' })
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 30_001)
  fireEvent.focus(window)
  await waitFor(() => expect(detail).toHaveBeenCalledTimes(2))
  fireEvent.click(screen.getByRole('button', { name: 'Назначить' }))
  await screen.findByText('Изменение сохранено')
  expect(detail).toHaveBeenCalledTimes(3)
  await act(async () => pending.resolve(issue(1, 'Устаревший ответ')))
  expect(screen.queryByText('Устаревший ответ')).not.toBeInTheDocument()
  expect(screen.getByText('Изменение сохранено')).toBeVisible()
})

it('keeps the expanded fresh list when an old next-page request finishes after a mutation', async () => {
  const oldPage = deferred<ReturnType<typeof page>>()
  const list = vi.spyOn(api, 'trackerIssues').mockResolvedValueOnce(page(0)).mockReturnValueOnce(oldPage.promise)
    .mockImplementation(async query => page(query.offset ?? 0, 120, 'После изменения'))
  render(tree())
  fireEvent.click(await screen.findByRole('button', { name: 'Открыть задачу RP-1: Задача 1' }))
  await screen.findByRole('button', { name: 'Назначить' })
  fireEvent.click(screen.getByRole('button', { name: ru.tracker.showMore }))
  fireEvent.click(screen.getByRole('button', { name: 'Назначить' }))
  await screen.findByRole('button', { name: 'Открыть задачу RP-100: После изменения 100' })
  await act(async () => oldPage.resolve(page(50)))
  expect(screen.getByRole('button', { name: 'Открыть задачу RP-100: После изменения 100' })).toBeVisible()
  expect(list.mock.calls.map(([query]) => query.offset)).toEqual([0, 50, 0, 50])
})

// Keep lifecycle assertions deterministic; pollingCapacity tests exercise jitter.
beforeEach(() => { vi.spyOn(Math, 'random').mockReturnValue(0) })

beforeEach(() => { vi.stubGlobal('crypto', webcrypto) })
