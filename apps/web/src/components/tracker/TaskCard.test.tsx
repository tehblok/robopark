import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { TrackerComment, TrackerIssueDetail } from '../../api'
import { IssueActionsPanel } from './IssueActionsPanel'
import { IssueDetailPanel } from './IssueDetailPanel'
import { IssueRichText } from './IssueRichText'
import { commentDraftKey } from './useCommentDraft'
import { ru } from '../../i18n/ru'

const noop = async () => {}
const actions = { canWrite: true, transitions: [], currentUser: 'tracker-me', draftOwner: 'me', issueKey: 'TEST-1',
  onComment: noop, onAssign: noop, onUnassign: noop, onTransition: noop, onClose: noop }
const issue: TrackerIssueDetail = { key: 'TEST-1', summary: 'Проверка', status: 'Open', queue: 'TEST', url: 'https://example.org/TEST-1',
  capabilities: { comment: true, assign: true, unassign: true, transition: true, close: true, attach: true } }
const comment = (id: string, author_login = 'colleague'): TrackerComment => ({ id, text: `Комментарий ${id}`, author_login })

afterEach(() => { localStorage.clear(); vi.restoreAllMocks() })

describe('comment drafts', () => {
  it('restores drafts after remount and isolates accounts and issues', () => {
    const view = render(<IssueActionsPanel {...actions} />)
    fireEvent.change(screen.getByLabelText(ru.tracker.comments), { target: { value: 'Не отправлено' } })
    view.rerender(<IssueActionsPanel {...actions} issueKey="TEST-2" />)
    expect(screen.getByLabelText(ru.tracker.comments)).toHaveValue('')
    fireEvent.change(screen.getByLabelText(ru.tracker.comments), { target: { value: 'Другая задача' } })
    view.rerender(<IssueActionsPanel {...actions} draftOwner="other" />)
    expect(screen.getByLabelText(ru.tracker.comments)).toHaveValue('')
    view.unmount()
    render(<IssueActionsPanel {...actions} />)
    expect(screen.getByLabelText(ru.tracker.comments)).toHaveValue('Не отправлено')
  })

  it('retains failure text and removes successfully sent drafts from storage', async () => {
    const onComment = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(undefined)
    render(<IssueActionsPanel {...actions} onComment={onComment} />)
    fireEvent.change(screen.getByLabelText(ru.tracker.comments), { target: { value: 'Текст' } })
    fireEvent.click(screen.getByRole('button', { name: ru.tracker.commentSubmit }))
    await screen.findByRole('alert')
    expect(screen.getByLabelText(ru.tracker.comments)).toHaveValue('Текст')
    fireEvent.click(screen.getByRole('button', { name: ru.tracker.commentSubmit }))
    await waitFor(() => expect(screen.getByLabelText(ru.tracker.comments)).toHaveValue(''))
    expect(localStorage.getItem(commentDraftKey('me', 'TEST-1')!)).toBeNull()
  })

  it('preserves newer edits when an older send finishes after switching issues and back', async () => {
    let finish!: () => void
    const onComment = () => new Promise<void>(resolve => { finish = resolve })
    const view = render(<IssueActionsPanel {...actions} onComment={onComment} />)
    fireEvent.change(screen.getByLabelText(ru.tracker.comments), { target: { value: 'Отправлено' } })
    fireEvent.click(screen.getByRole('button', { name: ru.tracker.commentSubmit }))
    view.rerender(<IssueActionsPanel {...actions} issueKey="TEST-2" />)
    view.rerender(<IssueActionsPanel {...actions} />)
    fireEvent.change(screen.getByLabelText(ru.tracker.comments), { target: { value: 'Следующий комментарий' } })
    await act(async () => finish())
    expect(screen.getByLabelText(ru.tracker.comments)).toHaveValue('Следующий комментарий')
    expect(JSON.parse(localStorage.getItem(commentDraftKey('me', 'TEST-1')!)!).text).toBe('Следующий комментарий')
  })

  it('clears the revisited composer after a late successful send without resurrecting it', async () => {
    let finish!: () => void
    const view = render(<IssueActionsPanel {...actions} onComment={() => new Promise<void>(resolve => { finish = resolve })} />)
    fireEvent.change(screen.getByLabelText(ru.tracker.comments), { target: { value: 'Отправлено' } })
    fireEvent.click(screen.getByRole('button', { name: ru.tracker.commentSubmit }))
    view.rerender(<IssueActionsPanel {...actions} issueKey="TEST-2" />)
    view.rerender(<IssueActionsPanel {...actions} />)
    await act(async () => finish())
    expect(screen.getByLabelText(ru.tracker.comments)).toHaveValue('')
    view.rerender(<IssueActionsPanel {...actions} issueKey="TEST-2" />)
    view.rerender(<IssueActionsPanel {...actions} />)
    expect(screen.getByLabelText(ru.tracker.comments)).toHaveValue('')
  })

  it('reacts to another tab editing and clearing the same draft only', () => {
    render(<IssueActionsPanel {...actions} />)
    const key = commentDraftKey('me', 'TEST-1')!
    localStorage.setItem(key, JSON.stringify({ text: 'Из другой вкладки', revision: 'tab2' }))
    fireEvent(window, new StorageEvent('storage', { key }))
    expect(screen.getByLabelText(ru.tracker.comments)).toHaveValue('Из другой вкладки')
    localStorage.removeItem(key)
    fireEvent(window, new StorageEvent('storage', { key }))
    expect(screen.getByLabelText(ru.tracker.comments)).toHaveValue('')
  })

  it('clears an in-memory sent draft even when quota prevented overwriting an older stored draft', async () => {
    const key = commentDraftKey('me', 'TEST-1')!
    localStorage.setItem(key, JSON.stringify({ text: 'Старый текст', revision: 'old' }))
    render(<IssueActionsPanel {...actions} />)
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
    fireEvent.change(screen.getByLabelText(ru.tracker.comments), { target: { value: 'Новый текст' } })
    fireEvent.click(screen.getByRole('button', { name: ru.tracker.commentSubmit }))
    await waitFor(() => expect(screen.getByLabelText(ru.tracker.comments)).toHaveValue(''))
    expect(localStorage.getItem(key)).toBeNull()
  })

  it('keeps editing and sending available when localStorage throws', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('blocked') })
    render(<IssueActionsPanel {...actions} />)
    fireEvent.change(screen.getByLabelText(ru.tracker.comments), { target: { value: 'Текст' } })
    expect(screen.getByLabelText(ru.tracker.comments)).toHaveValue('Текст')
    fireEvent.click(screen.getByRole('button', { name: ru.tracker.commentSubmit }))
    await waitFor(() => expect(screen.getByLabelText(ru.tracker.comments)).toHaveValue(''))
  })
})

describe('safe task Markdown', () => {
  it('renders emphasis, lists, links and code while treating HTML and unsafe URLs as inert text', () => {
    const { container } = render(<IssueRichText text={'**Важно** и *курсив*\n\n- Один\n- Два\n\n[Ссылка](https://example.org) [опасно](javascript:alert%281%29)\n\n`inline`\n\n```js\nconst x = 1\n```\n\n<img src=x onerror=alert(1)>\n<script>alert(1)</script>'} />)
    expect(container.querySelector('strong')).toHaveTextContent('Важно')
    expect(container.querySelector('em')).toHaveTextContent('курсив')
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
    expect(screen.getByRole('link', { name: 'Ссылка' })).toHaveAttribute('href', 'https://example.org')
    expect(screen.queryByRole('link', { name: 'опасно' })).not.toBeInTheDocument()
    expect(container.querySelector('pre code')).toHaveTextContent('const x = 1')
    expect(container.querySelector('img, script')).toBeNull()
    expect(container).toHaveTextContent('<img src=x onerror=alert(1)>')
  })

  it('expands long descriptions accessibly and keeps comments fully visible', () => {
    render(<IssueRichText collapsible text={`${'Подробности '.repeat(120)}Конец описания`} />)
    expect(screen.queryByText(/Конец описания/)).not.toBeInTheDocument()
    const expand = screen.getByRole('button', { name: 'Показать описание полностью' })
    expect(expand).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(expand)
    expect(screen.getByText(/Конец описания/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Свернуть описание' })).toHaveAttribute('aria-expanded', 'true')
  })
})

describe('incoming task updates', () => {
  const detail = (comments: TrackerComment[], options: Partial<React.ComponentProps<typeof IssueDetailPanel>> = {}) =>
    <MemoryRouter><IssueDetailPanel issue={issue} comments={comments} currentUser="me" accountKey="me" showRobotCheck={false} {...options} /></MemoryRouter>

  it('initializes after comments finish loading, counts colleagues only and never scrolls automatically', () => {
    const scroll = vi.fn()
    HTMLElement.prototype.scrollIntoView = scroll
    const view = render(detail([], { commentsLoading: true }))
    view.rerender(detail([comment('1')]))
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    view.rerender(detail([comment('1'), comment('2'), comment('3', 'me')]))
    expect(screen.getByRole('button', { name: '1 новый комментарий — перейти' })).toBeInTheDocument()
    expect(scroll).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '1 новый комментарий — перейти' }))
    expect(scroll).toHaveBeenCalledOnce()
    expect(document.activeElement).toHaveAttribute('data-comment-id', '2')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    view.rerender(detail([comment('1'), comment('2'), comment('3', 'me')]))
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it.each<Partial<TrackerIssueDetail>>([
    { status: 'Closed' }, { description: '**Новое описание**' },
    { assignee: { login: 'colleague', display: 'Коллега' } },
  ])('notifies about changed task fields %j', change => {
    const view = render(detail([]))
    view.rerender(detail([], { issue: { ...issue, ...change } }))
    expect(screen.getByRole('status')).toHaveTextContent('Задача обновлена')
  })

  it('detects actual issue changes once, ignores timestamps and resets on account/issue switch', () => {
    const view = render(detail([comment('1')]))
    view.rerender(detail([comment('1')], { issue: { ...issue, updated_at: '2026-09-06T12:00:00Z' } }))
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    view.rerender(detail([comment('1')], { issue: { ...issue, summary: 'Новое название' } }))
    expect(screen.getByRole('status')).toHaveTextContent('Задача обновлена')
    fireEvent.click(screen.getByRole('button', { name: 'Скрыть уведомление' }))
    view.rerender(detail([comment('1')], { issue: { ...issue, summary: 'Новое название' } }))
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    view.rerender(detail([comment('2')], { issue: { ...issue, key: 'TEST-2' } }))
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    view.rerender(detail([comment('1')], { accountKey: 'someone-else' }))
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })
})
