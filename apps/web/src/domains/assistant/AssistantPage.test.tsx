import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Link, MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ApiError, type User } from '../../api'
import { AuthContext } from '../../auth-context'
import { ParkScopeContext } from '../../app/park/parkScope'
import { AssistantPage } from './AssistantPage'
import { assistantErrorText, createKnowledgeImportBatches } from './assistantUi'
import type { AssistantApiClient, AiStatus } from './assistantApi'

const park = { id: 4, name: 'Север', tag: 'NORTH', timezone: 'Europe/Moscow' }
const southPark = { id: 5, name: 'Юг', tag: 'SOUTH', timezone: 'Europe/Moscow' }
const operator: User = { id: 1, username: 'operator', role: 'operator', access_status: 'approved', permissions: ['nav.tasks'], parks: [park] }
const admin: User = { ...operator, id: 2, username: 'admin', role: 'admin' }
const ready: AiStatus = {
  supported: true, installed: true, enabled: true, ready: true, reason: null,
  model: 'google/gemma-4-E4B-it-qat-q4_0-gguf', backend: 'cuda', can_manage: false,
  counts: { documents: 2, candidates: 1, jobs: 0 },
}

function client(status: AiStatus = ready): AssistantApiClient {
  return {
    status: vi.fn().mockResolvedValue(status),
    conversations: vi.fn().mockResolvedValue([]),
    conversation: vi.fn(), createConversation: vi.fn().mockResolvedValue({ id: 'c-1', title: 'Новый разговор', park_id: 4, issue_key: null, updated_at: '2026-10-04T10:00:00Z' }),
    deleteConversation: vi.fn(), sendMessage: vi.fn().mockResolvedValue({ id: 'j-1', kind: 'chat', state: 'queued', created_at: '', updated_at: '', error: null, result: null }),
    job: vi.fn().mockResolvedValue({ id: 'j-1', kind: 'chat', state: 'succeeded', created_at: '', updated_at: '', error: null, result: {} }), cancelJob: vi.fn(), confirmAction: vi.fn(),
    documents: vi.fn().mockResolvedValue({ items: [], total: 0, offset: 0, limit: 30 }), document: vi.fn(), createDocument: vi.fn(), updateDocument: vi.fn(), deleteDocument: vi.fn(), importDocuments: vi.fn(),
    config: vi.fn(), updateConfig: vi.fn(), runtime: vi.fn(), prompts: vi.fn(), updatePrompt: vi.fn(),
    connectors: vi.fn(), createConnector: vi.fn(), updateConnector: vi.fn(), deleteConnector: vi.fn(),
    scripts: vi.fn(), createScript: vi.fn(), updateScript: vi.fn(), deleteScript: vi.fn(), testScript: vi.fn(),
    createDraft: vi.fn(), automations: vi.fn(), createAutomation: vi.fn(), updateAutomation: vi.fn(), deleteAutomation: vi.fn(), previewAutomation: vi.fn(),
    runs: vi.fn(), purgeRuns: vi.fn(), maintenance: vi.fn(),
  }
}

function page(apiClient: AssistantApiClient, user: User = operator, path = '/assistant', activePark = park, navigationTarget?: string) {
  return <MemoryRouter initialEntries={[path]}><AuthContext.Provider value={{
    user, loading: false, login: vi.fn(), refreshUser: vi.fn(), logout: vi.fn(),
  }}><ParkScopeContext.Provider value={{
    parkId: activePark.id, selectedPark: activePark, parks: user.parks, loading: false, locked: false,
    setParkId: vi.fn(), refreshParks: vi.fn(),
  }}><AssistantPage apiClient={apiClient} />{navigationTarget ? <Link to={navigationTarget}>Следующий тикет</Link> : null}</ParkScopeContext.Provider></AuthContext.Provider></MemoryRouter>
}

function renderPage(apiClient: AssistantApiClient, user: User = operator, path = '/assistant') {
  return render(page(apiClient, user, path))
}

describe('AssistantPage', () => {
  it('ignores a late confirmation response after another conversation is selected', async () => {
    const apiClient = client()
    const pending = { id: 'j-1', kind: 'chat', state: 'waiting' as const, created_at: '', updated_at: '', error: null, result: null, actions: [{ id: 'a-1', tool: 'script_delete', state: 'waiting' as const, preview: 'Удалить старый скрипт', arguments: {}, result: null, error: null, digest: 'binding', expires_at: null, created_at: '' }] }
    const first = { id: 'c-1', title: 'Первый', park_id: 4, issue_key: null, updated_at: '', messages: [], jobs: [pending] }
    const second = { ...first, id: 'c-2', title: 'Второй', jobs: [] }
    vi.mocked(apiClient.conversations).mockResolvedValue([first, second])
    vi.mocked(apiClient.conversation).mockImplementation(async id => id === 'c-1' ? first : second)
    let finish!: (value: typeof pending) => void
    vi.mocked(apiClient.confirmAction).mockImplementation(() => new Promise(resolve => { finish = resolve }))
    renderPage(apiClient)
    await userEvent.click(await screen.findByRole('button', { name: 'Подтвердить действие' }))
    await userEvent.click(screen.getByRole('button', { name: 'Второй' }))
    await act(async () => finish(pending))
    expect(screen.getByRole('heading', { name: 'Второй' })).toBeVisible()
    expect(screen.queryByText('Удалить старый скрипт')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Сообщение помощнику')).toBeEnabled()
  })

  it('shows exact pending action and only confirms it after a click', async () => {
    const apiClient = client()
    const pending = { id: 'j-1', kind: 'chat', state: 'waiting' as const, created_at: '', updated_at: '', error: null, result: null, actions: [{ id: 'a-1', tool: 'script_delete', state: 'waiting' as const, preview: 'Удалить скрипт Mapping, ревизия 3', arguments: { script_id: 's-1', revision: 3 }, result: null, error: null, digest: 'abc123', expires_at: '2027-01-01T00:00:00Z', created_at: '' }] }
    const conversation = { id: 'c-1', title: 'Ремонт', park_id: 4, issue_key: null, updated_at: '' }
    vi.mocked(apiClient.conversations).mockResolvedValue([conversation])
    vi.mocked(apiClient.conversation).mockResolvedValue({ ...conversation, messages: [], jobs: [pending] })
    vi.mocked(apiClient.confirmAction).mockResolvedValue({ ...pending, state: 'queued', actions: [{ ...pending.actions[0], state: 'approved', digest: null }] })
    renderPage(apiClient)
    expect(await screen.findByText('Удалить скрипт Mapping, ревизия 3')).toBeVisible()
    expect(apiClient.confirmAction).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Подтвердить действие' }))
    expect(apiClient.confirmAction).toHaveBeenCalledExactlyOnceWith('a-1', 'abc123')
  })

  it('keeps a completed action visible when the following model answer fails', async () => {
    const apiClient = client()
    const conversation = { id: 'c-1', title: 'Ремонт', park_id: 4, issue_key: null, updated_at: '' }
    vi.mocked(apiClient.conversations).mockResolvedValue([conversation])
    vi.mocked(apiClient.conversation).mockResolvedValue({ ...conversation, messages: [], jobs: [{ id: 'j-1', kind: 'chat', state: 'failed', created_at: '', updated_at: '', error: 'ai_context_too_large', result: null, actions: [{ id: 'a-1', tool: 'script_create', state: 'succeeded', preview: 'Создать скрипт Mapping', arguments: {}, result: { id: 's-1', enabled: false }, error: null, digest: null, expires_at: null, created_at: '' }] }] })
    renderPage(apiClient)
    expect(await screen.findByText('Создать скрипт Mapping')).toBeVisible()
    expect(screen.getByText('Выполнено')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Подтвердить действие' })).not.toBeInTheDocument()
  })

  it('shows the AGX requirement without making chat or import calls on unsupported hardware', async () => {
    const apiClient = client({ ...ready, supported: false, ready: false, reason: 'AGX Orin required' })
    renderPage(apiClient)

    expect(await screen.findByRole('heading', { name: 'Требуется NVIDIA AGX Orin' })).toBeVisible()
    expect(apiClient.conversations).not.toHaveBeenCalled()
    expect(apiClient.documents).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: /отправить/i })).not.toBeInTheDocument()
  })

  it('keeps management tabs hidden when authoritative status denies management', async () => {
    renderPage(client(), { ...operator, role: 'admin' })

    expect(await screen.findByRole('tab', { name: 'Помощник' })).toBeVisible()
    expect(screen.getByRole('tab', { name: 'База знаний' })).toBeVisible()
    expect(screen.queryByRole('tab', { name: 'Автоматизации' })).not.toBeInTheDocument()
  })

  it('shows initial knowledge import progress without exposing backend details', async () => {
    const apiClient = client({
      ...ready,
      knowledge_bundle: { state: 'importing', total: 15595, processed: 4100, created: 4050, skipped: 50, error: '/private/source/seed.jsonl' },
    })
    renderPage(apiClient)

    await userEvent.click(await screen.findByRole('tab', { name: 'База знаний' }))

    expect(screen.getByRole('progressbar', { name: 'Загрузка начальной базы знаний' })).toHaveAttribute('aria-valuenow', '4100')
    expect(screen.getByText('Загружаем начальную базу знаний: 4100 из 15595.')).toBeVisible()
    expect(screen.queryByText(/private\/source/)).not.toBeInTheDocument()
  })

  it('keeps the knowledge section compatible with status responses without bundle state', async () => {
    renderPage(client(ready))

    await userEvent.click(await screen.findByRole('tab', { name: 'База знаний' }))

    expect(screen.getByRole('heading', { name: 'Поиск' })).toBeVisible()
    expect(screen.queryByText(/начальн.*баз/i)).not.toBeInTheDocument()
  })

  it('replaces a bundle failure detail with a safe recovery message', async () => {
    const apiClient = client({
      ...ready,
      knowledge_bundle: { state: 'failed', total: 15595, processed: 4100, created: 4050, skipped: 50, error: 'permission denied: /private/source/seed.jsonl' },
    })
    renderPage(apiClient)

    await userEvent.click(await screen.findByRole('tab', { name: 'База знаний' }))

    expect(screen.getByRole('alert')).toHaveTextContent('Начальную базу знаний загрузить не удалось')
    expect(screen.queryByText(/permission denied|private\/source/i)).not.toBeInTheDocument()
  })

  it('polls supported status and reflects readiness changes', async () => {
    const apiClient = client()
    const starting = { ...ready, ready: false, reason: 'starting' }
    const disabled = { ...ready, ready: false, enabled: false, reason: 'disabled' }
    vi.mocked(apiClient.status).mockResolvedValueOnce(starting).mockResolvedValueOnce(ready).mockResolvedValue(disabled)
    let tick!: () => Promise<void>
    const interval = vi.spyOn(window, 'setInterval').mockImplementation((handler, delay) => { if (delay === 5000) tick = handler as () => Promise<void>; return 123 as unknown as ReturnType<typeof setInterval> })
    renderPage(apiClient)
    expect(await screen.findByText('Запускаем локальную модель.')).toBeVisible()
    await waitFor(() => expect(tick).toBeTypeOf('function'))

    await act(async () => { await tick() })
    expect(apiClient.status).toHaveBeenCalledTimes(2)
    await waitFor(() => expect(screen.queryByText('Запускаем локальную модель.')).not.toBeInTheDocument())
    expect(screen.getByLabelText('Сообщение помощнику')).toBeEnabled()

    await act(async () => { await tick() })
    await waitFor(() => expect(apiClient.status).toHaveBeenCalledTimes(3))
    expect(await screen.findByText(/Помощник выключен/)).toBeVisible()
    interval.mockRestore()
  })

  it('does not overlap supported status polls', async () => {
    const apiClient = client()
    const starting = { ...ready, ready: false, reason: 'starting' }
    let finishPoll!: (status: AiStatus) => void
    vi.mocked(apiClient.status).mockResolvedValueOnce(starting).mockImplementationOnce(() => new Promise(resolve => { finishPoll = resolve }))
    let tick!: () => Promise<void>
    const interval = vi.spyOn(window, 'setInterval').mockImplementation((handler, delay) => { if (delay === 5000) tick = handler as () => Promise<void>; return 123 as unknown as ReturnType<typeof setInterval> })
    renderPage(apiClient)
    expect(await screen.findByText('Запускаем локальную модель.')).toBeVisible()
    await waitFor(() => expect(tick).toBeTypeOf('function'))

    let firstPoll!: Promise<void>
    await act(async () => { firstPoll = tick(); await tick() })
    expect(apiClient.status).toHaveBeenCalledTimes(2)
    await act(async () => { finishPoll(ready); await firstPoll })
    interval.mockRestore()
  })

  it.each([401, 403])('hides protected assistant panels after a %s status poll', async statusCode => {
    const apiClient = client()
    vi.mocked(apiClient.status).mockResolvedValueOnce(ready).mockRejectedValue(new ApiError(statusCode, 'forbidden'))
    let tick!: () => Promise<void>
    const interval = vi.spyOn(window, 'setInterval').mockImplementation((handler, delay) => {
      if (delay === 5000) tick = handler as () => Promise<void>
      return 123 as unknown as ReturnType<typeof setInterval>
    })
    try {
      renderPage(apiClient)
      const draft = await screen.findByRole('textbox', { name: 'Сообщение помощнику' })
      fireEvent.change(draft, { target: { value: 'Private repair context' } })
      await act(async () => tick())
      expect(screen.queryByRole('textbox', { name: 'Сообщение помощнику' })).not.toBeInTheDocument()
      expect(screen.queryByRole('heading', { name: 'Локальный помощник' })).not.toBeInTheDocument()
      expect(screen.getByRole('alert')).toBeVisible()
    } finally { interval.mockRestore() }
  })

  it('keeps the current draft after a transient status poll error', async () => {
    const apiClient = client()
    vi.mocked(apiClient.status).mockResolvedValueOnce(ready).mockRejectedValue(new ApiError(503, 'unavailable'))
    let tick!: () => Promise<void>
    const interval = vi.spyOn(window, 'setInterval').mockImplementation((handler, delay) => {
      if (delay === 5000) tick = handler as () => Promise<void>
      return 123 as unknown as ReturnType<typeof setInterval>
    })
    try {
      renderPage(apiClient)
      const draft = await screen.findByRole('textbox', { name: 'Сообщение помощнику' })
      fireEvent.change(draft, { target: { value: 'Pending repair question' } })
      await act(async () => tick())
      expect(draft).toHaveValue('Pending repair question')
      expect(screen.getByRole('heading', { name: 'Локальный помощник' })).toBeVisible()
    } finally { interval.mockRestore() }
  })

  it('does not reuse a previous identity status when the next identity is denied', async () => {
    const apiClient = client()
    const nextUser = { ...operator, id: 9, username: 'next-operator' }
    vi.mocked(apiClient.status).mockResolvedValueOnce(ready).mockRejectedValueOnce(new ApiError(403, 'forbidden'))
    const interval = vi.spyOn(window, 'setInterval').mockImplementation(() => 123 as unknown as ReturnType<typeof setInterval>)
    try {
      const view = render(page(apiClient, operator))
      expect(await screen.findByRole('textbox', { name: 'Сообщение помощнику' })).toBeVisible()
      expect(apiClient.conversations).toHaveBeenCalledTimes(1)
      expect(apiClient.documents).toHaveBeenCalledTimes(1)
      const initialStatusIntervalCalls = interval.mock.calls.filter(([, delay]) => delay === 5000).length

      view.rerender(page(apiClient, nextUser))
      expect(await screen.findByRole('alert')).toBeVisible()
      expect(screen.queryByRole('heading', { name: 'Локальный помощник' })).not.toBeInTheDocument()
      expect(apiClient.conversations).toHaveBeenCalledTimes(1)
      expect(apiClient.documents).toHaveBeenCalledTimes(1)
      expect(interval.mock.calls.filter(([, delay]) => delay === 5000)).toHaveLength(initialStatusIntervalCalls)
    } finally { interval.mockRestore() }
  })

  it('aborts the initial status request when the page unmounts', () => {
    const apiClient = client()
    vi.mocked(apiClient.status).mockImplementation(() => new Promise(() => {}))

    const view = renderPage(apiClient)
    const signal = (vi.mocked(apiClient.status).mock.calls[0] as unknown[])[0] as AbortSignal
    view.unmount()

    expect(signal).toBeInstanceOf(AbortSignal)
    expect(signal.aborted).toBe(true)
  })

  it('aborts an in-flight status poll when the page unmounts', async () => {
    const apiClient = client()
    vi.mocked(apiClient.status).mockResolvedValueOnce(ready).mockImplementation(() => new Promise(() => {}))
    let tick!: () => Promise<void>
    const interval = vi.spyOn(window, 'setInterval').mockImplementation((handler, delay) => { if (delay === 5000) tick = handler as () => Promise<void>; return 123 as unknown as ReturnType<typeof setInterval> })
    const view = renderPage(apiClient)
    await screen.findByRole('tab', { name: 'Помощник' })
    await waitFor(() => expect(tick).toBeTypeOf('function'))

    void tick()
    await waitFor(() => expect(apiClient.status).toHaveBeenCalledTimes(2))
    const signal = (vi.mocked(apiClient.status).mock.calls[1] as unknown[])[0] as AbortSignal
    view.unmount()

    expect(signal).toBeInstanceOf(AbortSignal)
    expect(signal.aborted).toBe(true)
    interval.mockRestore()
  })

  it('does not run a queued status poll after the page unmounts', async () => {
    const apiClient = client()
    let tick!: () => Promise<void>
    const interval = vi.spyOn(window, 'setInterval').mockImplementation((handler, delay) => {
      if (delay === 5000) tick = handler as () => Promise<void>
      return 123 as unknown as ReturnType<typeof setInterval>
    })
    try {
      const view = renderPage(apiClient)
      await screen.findByRole('tab', { name: 'Помощник' })
      await waitFor(() => expect(tick).toBeTypeOf('function'))
      view.unmount()

      await act(async () => tick())
      expect(apiClient.status).toHaveBeenCalledTimes(1)
    } finally { interval.mockRestore() }
  })

  it('keeps the newest park documents when an older load resolves late', async () => {
    const apiClient = client()
    const currentUser = { ...operator, parks: [park, southPark] }
    let finishNorth!: (value: Awaited<ReturnType<AssistantApiClient['documents']>>) => void
    let finishSouth!: (value: Awaited<ReturnType<AssistantApiClient['documents']>>) => void
    vi.mocked(apiClient.documents).mockImplementation((filters = {}) => new Promise(resolve => {
      if (filters.park_id === park.id) finishNorth = resolve
      else finishSouth = resolve
    }))
    const view = render(page(apiClient, currentUser, '/assistant', park))
    await userEvent.click(await screen.findByRole('tab', { name: 'База знаний' }))
    await waitFor(() => expect(apiClient.documents).toHaveBeenCalledTimes(1))

    view.rerender(page(apiClient, currentUser, '/assistant', southPark))
    await waitFor(() => expect(apiClient.documents).toHaveBeenCalledTimes(2))
    const northSignal = ((vi.mocked(apiClient.documents).mock.calls[0] as unknown[])[0] as { signal?: AbortSignal }).signal
    await act(async () => finishSouth({ items: [{ id: 'south', title: 'Южная инструкция', kind: 'manual', state: 'active', trust: 'instruction', park_id: 5, source_ref: 'south', updated_at: '', revision: 1 }], total: 1, offset: 0, limit: 30 }))
    expect(await screen.findByText('Южная инструкция')).toBeVisible()
    await act(async () => finishNorth({ items: [{ id: 'north', title: 'Северная инструкция', kind: 'manual', state: 'active', trust: 'instruction', park_id: 4, source_ref: 'north', updated_at: '', revision: 1 }], total: 1, offset: 0, limit: 30 }))

    expect(northSignal).toBeInstanceOf(AbortSignal)
    expect(northSignal?.aborted).toBe(true)
    expect(screen.getByText('Южная инструкция')).toBeVisible()
    expect(screen.queryByText('Северная инструкция')).not.toBeInTheDocument()
  })

  it('ignores a late status response from the previous user', async () => {
    const apiClient = client()
    const nextUser = { ...operator, id: 9, username: 'next-operator' }
    let finishPrevious!: (value: AiStatus) => void
    let finishNext!: (value: AiStatus) => void
    vi.mocked(apiClient.status)
      .mockImplementationOnce(() => new Promise(resolve => { finishPrevious = resolve }))
      .mockImplementationOnce(() => new Promise(resolve => { finishNext = resolve }))
    const view = render(page(apiClient, operator))
    view.rerender(page(apiClient, nextUser))
    await waitFor(() => expect(apiClient.status).toHaveBeenCalledTimes(2))

    await act(async () => finishNext(ready))
    expect(await screen.findByRole('tab', { name: 'Помощник' })).toBeVisible()
    await act(async () => finishPrevious({ ...ready, supported: false, ready: false, reason: 'old-user-response' }))

    expect(screen.getByRole('tab', { name: 'Помощник' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Требуется NVIDIA AGX Orin' })).not.toBeInTheDocument()
  })

  it('preserves a failed chat draft and renders returned source citations after retry', async () => {
    const apiClient = client()
    vi.mocked(apiClient.sendMessage).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ id: 'j-1', kind: 'chat', state: 'queued', created_at: '', updated_at: '', error: null, result: null })
    vi.mocked(apiClient.conversation).mockResolvedValue({
      id: 'c-1', title: 'Новый разговор', park_id: 4, issue_key: null, updated_at: '',
      messages: [{ id: 'm-2', role: 'assistant', content: 'Проверьте разъём.', created_at: '', sources: [{ id: 'd-1', title: 'Инструкция по лидару', excerpt: 'Отключите питание', trust: 'instruction' }] }], jobs: [],
    })
    renderPage(apiClient)
    const user = userEvent.setup()

    const input = await screen.findByLabelText('Сообщение помощнику')
    await waitFor(() => expect(input).toBeEnabled())
    await user.type(input, 'Как проверить лидар?')
    await user.click(screen.getByRole('button', { name: 'Отправить' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось отправить')
    expect(input).toHaveValue('Как проверить лидар?')

    await user.click(screen.getByRole('button', { name: 'Отправить' }))
    await waitFor(() => expect(apiClient.conversation).toHaveBeenCalledWith('c-1', expect.any(AbortSignal)))
    expect(await screen.findByRole('link', { name: 'Инструкция по лидару' })).toBeVisible()
    expect(input).toHaveValue('')
  })

  it('opens a cited source directly from the assistant URL', async () => {
    const apiClient = client()
    vi.mocked(apiClient.document).mockResolvedValue({
      id: 'd-1', title: 'Инструкция по лидару', kind: 'manual', state: 'active', trust: 'instruction',
      park_id: null, source_ref: 'kb-1', updated_at: '', revision: 1, content: 'Отключите питание перед проверкой разъёма.',
    })

    renderPage(apiClient, operator, '/assistant?document=d-1')

    expect(await screen.findByRole('heading', { name: 'Инструкция по лидару' })).toBeVisible()
    expect(screen.getByText('Отключите питание перед проверкой разъёма.')).toBeVisible()
  })

  it('opens the conversation matching both issue and park instead of the first session', async () => {
    const apiClient = client()
    vi.mocked(apiClient.conversations).mockResolvedValue([
      { id: 'c-a', title: 'RP-A', park_id: 4, issue_key: 'RP-A', updated_at: '' },
      { id: 'c-b', title: 'RP-B', park_id: 4, issue_key: 'RP-B', updated_at: '' },
    ])
    vi.mocked(apiClient.conversation).mockImplementation(async id => ({
      id, title: id === 'c-b' ? 'RP-B' : 'RP-A', park_id: 4, issue_key: id === 'c-b' ? 'RP-B' : 'RP-A',
      updated_at: '', messages: [], jobs: [],
    }))

    renderPage(apiClient, operator, '/assistant?issue_key=RP-B&park_id=4')

    await waitFor(() => expect(apiClient.conversation).toHaveBeenCalledWith('c-b', expect.any(AbortSignal)))
    expect(apiClient.conversation).not.toHaveBeenCalledWith('c-a', expect.anything())
    expect(apiClient.createConversation).not.toHaveBeenCalled()
  })

  it('creates a selected issue conversation when unrelated sessions already exist', async () => {
    const apiClient = client()
    vi.mocked(apiClient.conversations).mockResolvedValue([
      { id: 'c-a', title: 'RP-A', park_id: 4, issue_key: 'RP-A', updated_at: '' },
    ])
    vi.mocked(apiClient.createConversation).mockResolvedValue({ id: 'c-b', title: 'RP-B', park_id: 4, issue_key: 'RP-B', updated_at: '' })
    vi.mocked(apiClient.conversation).mockResolvedValue({ id: 'c-b', title: 'RP-B', park_id: 4, issue_key: 'RP-B', updated_at: '', messages: [], jobs: [] })

    renderPage(apiClient, operator, '/assistant?issue_key=RP-B&park_id=4')

    await waitFor(() => expect(apiClient.createConversation).toHaveBeenCalledWith({ park_id: 4, issue_key: 'RP-B', title: 'RP-B' }))
    expect(await screen.findByRole('heading', { name: 'RP-B' })).toBeVisible()
  })

  it('keeps one automatic issue conversation while creation is pending', async () => {
    const apiClient = client()
    let finish!: (value: Awaited<ReturnType<AssistantApiClient['createConversation']>>) => void
    const conversation = { id: 'c-new', title: 'RP-NEW', park_id: 4, issue_key: 'RP-NEW', updated_at: '' }
    vi.mocked(apiClient.createConversation).mockImplementation(() => new Promise(resolve => { finish = resolve }))
    vi.mocked(apiClient.conversation).mockResolvedValue({ ...conversation, messages: [], jobs: [] })
    renderPage(apiClient, operator, '/assistant?issue_key=RP-NEW&park_id=4')
    await waitFor(() => expect(apiClient.createConversation).toHaveBeenCalledTimes(1))
    expect(screen.getByRole('button', { name: 'Новый' })).toBeDisabled()
    expect(screen.getByLabelText('Сообщение помощнику')).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Новый' }))
    expect(apiClient.createConversation).toHaveBeenCalledTimes(1)
    await act(async () => finish(conversation))
    await waitFor(() => expect(screen.getByLabelText('Сообщение помощнику')).toBeEnabled())
    expect(screen.getByRole('heading', { name: 'RP-NEW' })).toBeVisible()
    expect(apiClient.createConversation).toHaveBeenCalledTimes(1)
  })

  it('coalesces rapid New clicks into one conversation creation', async () => {
    const apiClient = client()
    let finish!: (value: Awaited<ReturnType<AssistantApiClient['createConversation']>>) => void
    vi.mocked(apiClient.createConversation).mockImplementation(() => new Promise(resolve => { finish = resolve }))
    renderPage(apiClient)
    const create = await screen.findByRole('button', { name: 'Новый' })
    await waitFor(() => expect(create).toBeEnabled())
    act(() => { fireEvent.click(create); fireEvent.click(create) })
    expect(apiClient.createConversation).toHaveBeenCalledTimes(1)
    await act(async () => finish({ id: 'c-new', title: 'Новый разговор', park_id: 4, issue_key: null, updated_at: '' }))
    expect(create).toBeEnabled()
  })

  it('ignores a late created conversation after the user selects another conversation', async () => {
    const apiClient = client()
    const first = { id: 'c-1', title: 'Первый разговор', park_id: 4, issue_key: null, updated_at: '', messages: [], jobs: [] }
    const second = { ...first, id: 'c-2', title: 'Второй разговор' }
    vi.mocked(apiClient.conversations).mockResolvedValue([first, second])
    vi.mocked(apiClient.conversation).mockImplementation(async id => id === first.id ? first : second)
    let finish!: (value: Awaited<ReturnType<AssistantApiClient['createConversation']>>) => void
    vi.mocked(apiClient.createConversation).mockImplementation(() => new Promise(resolve => { finish = resolve }))
    renderPage(apiClient)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Новый' })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: 'Новый' }))
    await userEvent.click(screen.getByRole('button', { name: 'Второй разговор' }))
    await screen.findByRole('heading', { name: 'Второй разговор' })
    await act(async () => finish({ id: 'c-late', title: 'Поздний разговор', park_id: 4, issue_key: null, updated_at: '' }))
    expect(screen.getByRole('heading', { name: 'Второй разговор' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Поздний разговор' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Поздний разговор' })).not.toBeInTheDocument()
  })

  it.each(['manual', 'issue'] as const)('recovers from a failed %s conversation creation on an explicit retry', async mode => {
    const apiClient = client()
    const issueKey = mode === 'issue' ? 'RP-RETRY' : null
    const conversation = { id: 'c-retry', title: 'Повторный разговор', park_id: 4, issue_key: issueKey, updated_at: '' }
    vi.mocked(apiClient.createConversation).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(conversation)
    vi.mocked(apiClient.conversation).mockResolvedValue({ ...conversation, messages: [], jobs: [] })
    renderPage(apiClient, operator, issueKey ? `/assistant?issue_key=${issueKey}&park_id=4` : '/assistant')
    const create = await screen.findByRole('button', { name: 'Новый' })
    const input = screen.getByLabelText('Сообщение помощнику')
    if (mode === 'manual') {
      await waitFor(() => expect(create).toBeEnabled())
      await userEvent.type(input, 'Проверить питание')
      await userEvent.click(create)
    }
    expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось создать разговор')
    expect(apiClient.createConversation).toHaveBeenCalledTimes(1)
    expect(create).toBeEnabled()
    expect(input).toBeEnabled()
    if (mode === 'manual') expect(input).toHaveValue('Проверить питание')
    await userEvent.click(create)
    expect(await screen.findByRole('heading', { name: 'Повторный разговор' })).toBeVisible()
    expect(apiClient.createConversation).toHaveBeenCalledTimes(2)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(input).toBeEnabled()
    if (mode === 'manual') expect(input).toHaveValue('Проверить питание')
  })

  it('keeps the new issue conversation when an automatic creation for the old issue resolves last', async () => {
    const apiClient = client()
    const first = { id: 'c-a', title: 'RP-A', park_id: 4, issue_key: 'RP-A', updated_at: '' }
    const second = { ...first, id: 'c-b', title: 'RP-B', issue_key: 'RP-B' }
    let finishFirst!: (value: typeof first) => void
    vi.mocked(apiClient.createConversation).mockImplementationOnce(() => new Promise(resolve => { finishFirst = resolve })).mockResolvedValueOnce(second)
    vi.mocked(apiClient.conversation).mockResolvedValue({ ...second, messages: [], jobs: [] })
    render(page(apiClient, operator, '/assistant?issue_key=RP-A&park_id=4', park, '/assistant?issue_key=RP-B&park_id=4'))
    await waitFor(() => expect(apiClient.createConversation).toHaveBeenCalledTimes(1))
    await userEvent.click(screen.getByRole('link', { name: 'Следующий тикет' }))
    await screen.findByRole('heading', { name: 'RP-B' })
    await waitFor(() => expect(screen.getByLabelText('Сообщение помощнику')).toBeEnabled())
    await act(async () => finishFirst(first))
    expect(screen.getByRole('heading', { name: 'RP-B' })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'RP-A RP-A' })).not.toBeInTheDocument()
    expect(apiClient.createConversation).toHaveBeenCalledTimes(2)
    expect(apiClient.conversation).not.toHaveBeenCalledWith('c-a', expect.anything())
  })

  it('keeps the draft and allows retry when creating a conversation during submit fails', async () => {
    const apiClient = client()
    vi.mocked(apiClient.createConversation).mockRejectedValueOnce(new Error('offline'))
    renderPage(apiClient)
    const input = await screen.findByLabelText('Сообщение помощнику')
    await waitFor(() => expect(input).toBeEnabled())
    await userEvent.type(input, 'Проверить питание')
    await userEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось создать разговор')
    expect(apiClient.createConversation).toHaveBeenCalledTimes(1)
    expect(apiClient.sendMessage).not.toHaveBeenCalled()
    expect(input).toHaveValue('Проверить питание')
    expect(input).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Отправить' })).toBeEnabled()
  })

  it('does not open an issue conversation created after the page unmounts', async () => {
    const apiClient = client()
    let finishCreate!: (value: Awaited<ReturnType<AssistantApiClient['createConversation']>>) => void
    vi.mocked(apiClient.createConversation).mockImplementation(() => new Promise(resolve => { finishCreate = resolve }))
    const view = renderPage(apiClient, operator, '/assistant?issue_key=RP-LATE&park_id=4')
    await waitFor(() => expect(apiClient.createConversation).toHaveBeenCalledTimes(1))

    view.unmount()
    await act(async () => finishCreate({ id: 'c-late', title: 'RP-LATE', park_id: 4, issue_key: 'RP-LATE', updated_at: '' }))

    expect(apiClient.conversation).not.toHaveBeenCalled()
  })

  it('keeps the newly selected conversation when an older job resolves late', async () => {
    const apiClient = client()
    const first = { id: 'c-1', title: 'Первый разговор', park_id: 4, issue_key: null, updated_at: '', messages: [], jobs: [] }
    const second = { id: 'c-2', title: 'Второй разговор', park_id: 4, issue_key: null, updated_at: '', messages: [], jobs: [] }
    vi.mocked(apiClient.conversations).mockResolvedValue([first, second])
    let finishSecond!: (value: typeof second) => void
    let firstReads = 0
    vi.mocked(apiClient.conversation).mockImplementation(id => {
      if (id === 'c-2') return new Promise(resolve => { finishSecond = resolve })
      firstReads += 1
      return Promise.resolve(firstReads === 1 ? first : {
        ...first,
        messages: [{ id: 'm-old', role: 'assistant', content: 'Поздний старый ответ', sources: [], created_at: '' }],
      })
    })
    let finishJob!: (value: Awaited<ReturnType<AssistantApiClient['job']>>) => void
    vi.mocked(apiClient.job).mockImplementation(() => new Promise(resolve => { finishJob = resolve }))
    renderPage(apiClient)
    const input = await screen.findByLabelText('Сообщение помощнику')
    await screen.findByRole('heading', { name: 'Первый разговор' })
    fireEvent.change(input, { target: { value: 'Проверить питание' } })
    fireEvent.submit(input.closest('form')!)
    await waitFor(() => expect(apiClient.job).toHaveBeenCalledTimes(1))

    await userEvent.click(screen.getByRole('button', { name: 'Второй разговор' }))
    await act(async () => finishSecond(second))
    expect(await screen.findByRole('heading', { name: 'Второй разговор' })).toBeVisible()
    await act(async () => finishJob({ id: 'j-1', kind: 'chat', state: 'succeeded', created_at: '', updated_at: '', error: null, result: {} }))

    expect(screen.getByRole('heading', { name: 'Второй разговор' })).toBeVisible()
    expect(screen.queryByText('Поздний старый ответ')).not.toBeInTheDocument()
    expect(apiClient.cancelJob).not.toHaveBeenCalled()
  })

  it('ignores a final answer detail that resolves after selecting another conversation', async () => {
    const apiClient = client()
    const first = { id: 'c-1', title: 'Первый разговор', park_id: 4, issue_key: null, updated_at: '', messages: [], jobs: [] }
    const second = { ...first, id: 'c-2', title: 'Второй разговор' }
    vi.mocked(apiClient.conversations).mockResolvedValue([first, second])
    let finishAnswer!: (value: typeof first) => void
    let firstReads = 0
    vi.mocked(apiClient.conversation).mockImplementation(id => {
      if (id === second.id) return Promise.resolve(second)
      if (++firstReads === 1) return Promise.resolve(first)
      return new Promise(resolve => { finishAnswer = resolve })
    })
    renderPage(apiClient)
    const input = await screen.findByLabelText('Сообщение помощнику')
    await waitFor(() => expect(input).toBeEnabled())
    await userEvent.type(input, 'Проверить питание')
    await userEvent.click(screen.getByRole('button', { name: 'Отправить' }))
    await waitFor(() => expect(firstReads).toBe(2))
    await userEvent.click(screen.getByRole('button', { name: 'Второй разговор' }))
    await screen.findByRole('heading', { name: 'Второй разговор' })
    await act(async () => finishAnswer(first))
    expect(screen.getByRole('heading', { name: 'Второй разговор' })).toBeVisible()
    expect(input).toHaveValue('Проверить питание')
  })

  it('keeps the current selection when deleting an earlier selected conversation completes late', async () => {
    const apiClient = client()
    const first = { id: 'c-1', title: 'Первый разговор', park_id: 4, issue_key: null, updated_at: '', messages: [], jobs: [] }
    const second = { ...first, id: 'c-2', title: 'Второй разговор' }
    vi.mocked(apiClient.conversations).mockResolvedValue([first, second])
    vi.mocked(apiClient.conversation).mockImplementation(async id => id === first.id ? first : second)
    let finishDelete!: () => void
    vi.mocked(apiClient.deleteConversation).mockImplementation(() => new Promise(resolve => { finishDelete = resolve }))
    renderPage(apiClient)
    await screen.findByRole('heading', { name: 'Первый разговор' })
    await userEvent.click(screen.getByRole('button', { name: 'Удалить Первый разговор' }))
    await userEvent.click(screen.getByRole('button', { name: 'Второй разговор' }))
    await screen.findByRole('heading', { name: 'Второй разговор' })
    await act(async () => finishDelete())
    expect(screen.getByRole('heading', { name: 'Второй разговор' })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Первый разговор' })).not.toBeInTheDocument()
  })

  it('keeps both conversations removed when parallel deletions finish in reverse order', async () => {
    const apiClient = client()
    const first = { id: 'c-1', title: 'Первый разговор', park_id: 4, issue_key: null, updated_at: '', messages: [], jobs: [] }
    const second = { ...first, id: 'c-2', title: 'Второй разговор' }
    vi.mocked(apiClient.conversations).mockResolvedValue([first, second])
    vi.mocked(apiClient.conversation).mockResolvedValue(first)
    const finishes = new Map<string, () => void>()
    vi.mocked(apiClient.deleteConversation).mockImplementation(id => new Promise(resolve => { finishes.set(id, resolve) }))
    renderPage(apiClient)
    await screen.findByRole('heading', { name: 'Первый разговор' })
    await userEvent.click(screen.getByRole('button', { name: 'Удалить Первый разговор' }))
    await userEvent.click(screen.getByRole('button', { name: 'Удалить Второй разговор' }))
    await act(async () => finishes.get(second.id)!())
    await act(async () => finishes.get(first.id)!())
    expect(screen.queryByRole('button', { name: 'Первый разговор' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Второй разговор' })).not.toBeInTheDocument()
    expect(screen.getByText('Начните новый разговор.')).toBeVisible()
  })

  it('does not reopen a deleted conversation when its initial detail request resolves late', async () => {
    const apiClient = client()
    const first = { id: 'c-1', title: 'Удалённый разговор', park_id: 4, issue_key: null, updated_at: '', messages: [], jobs: [] }
    vi.mocked(apiClient.conversations).mockResolvedValue([first])
    let finishOpen!: (value: typeof first) => void
    vi.mocked(apiClient.conversation).mockImplementation(() => new Promise(resolve => { finishOpen = resolve }))
    vi.mocked(apiClient.deleteConversation).mockResolvedValue(undefined)
    renderPage(apiClient)
    await waitFor(() => expect(apiClient.conversation).toHaveBeenCalledTimes(1))
    await userEvent.click(screen.getByRole('button', { name: 'Удалить Удалённый разговор' }))
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Удалённый разговор' })).not.toBeInTheDocument())
    await act(async () => finishOpen(first))
    expect(screen.queryByRole('heading', { name: 'Удалённый разговор' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Сообщение помощнику')).toBeEnabled()
  })

  it('coalesces duplicate delete clicks and exposes a failed deletion for retry', async () => {
    const apiClient = client()
    const first = { id: 'c-1', title: 'Первый разговор', park_id: 4, issue_key: null, updated_at: '', messages: [], jobs: [] }
    vi.mocked(apiClient.conversations).mockResolvedValue([first])
    vi.mocked(apiClient.conversation).mockResolvedValue(first)
    let failDelete!: (error: Error) => void
    vi.mocked(apiClient.deleteConversation).mockImplementationOnce(() => new Promise((_resolve, reject) => { failDelete = reject })).mockResolvedValue(undefined)
    renderPage(apiClient)
    const remove = await screen.findByRole('button', { name: 'Удалить Первый разговор' })
    act(() => { fireEvent.click(remove); fireEvent.click(remove) })
    expect(apiClient.deleteConversation).toHaveBeenCalledTimes(1)
    expect(remove).toBeDisabled()
    await act(async () => failDelete(new Error('offline')))
    expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось удалить разговор')
    expect(remove).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Первый разговор' })).toBeVisible()
    await userEvent.click(remove)
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Первый разговор' })).not.toBeInTheDocument())
    expect(apiClient.deleteConversation).toHaveBeenCalledTimes(2)
  })

  it('aborts resumed polling after successful deletion and discards its late result', async () => {
    const apiClient = client()
    const pending = { id: 'j-pending', kind: 'chat', state: 'running' as const, created_at: '', updated_at: '', error: null, result: null }
    const first = { id: 'c-1', title: 'Первый разговор', park_id: 4, issue_key: null, updated_at: '', messages: [], jobs: [pending] }
    vi.mocked(apiClient.conversations).mockResolvedValue([first])
    vi.mocked(apiClient.conversation).mockResolvedValue(first)
    let finishJob!: (value: Awaited<ReturnType<AssistantApiClient['job']>>) => void
    vi.mocked(apiClient.job).mockImplementation(() => new Promise(resolve => { finishJob = resolve }))
    vi.mocked(apiClient.deleteConversation).mockResolvedValue(undefined)
    renderPage(apiClient)
    await waitFor(() => expect(apiClient.job).toHaveBeenCalledTimes(1))
    const signal = vi.mocked(apiClient.job).mock.calls[0][1]!
    await userEvent.click(screen.getByRole('button', { name: 'Удалить Первый разговор' }))
    await waitFor(() => expect(signal.aborted).toBe(true))
    await act(async () => finishJob({ ...pending, state: 'succeeded' }))
    expect(apiClient.conversation).toHaveBeenCalledTimes(1)
    expect(apiClient.job).toHaveBeenCalledTimes(1)
    expect(apiClient.cancelJob).not.toHaveBeenCalled()
    expect(screen.queryByRole('heading', { name: 'Первый разговор' })).not.toBeInTheDocument()
    expect(screen.queryByText(/Ответ ещё готовится/)).not.toBeInTheDocument()
    expect(screen.getByLabelText('Сообщение помощнику')).toBeEnabled()
  })

  it('preserves a new conversation creation when deletion of the old selection finishes first', async () => {
    const apiClient = client()
    const first = { id: 'c-1', title: 'Первый разговор', park_id: 4, issue_key: null, updated_at: '', messages: [], jobs: [] }
    const next = { ...first, id: 'c-new', title: 'Новый ремонт' }
    vi.mocked(apiClient.conversations).mockResolvedValue([first])
    vi.mocked(apiClient.conversation).mockResolvedValue(first)
    let finishDelete!: () => void
    let finishCreate!: (value: typeof first) => void
    vi.mocked(apiClient.deleteConversation).mockImplementation(() => new Promise(resolve => { finishDelete = resolve }))
    vi.mocked(apiClient.createConversation).mockImplementation(() => new Promise(resolve => { finishCreate = resolve }))
    renderPage(apiClient)
    const create = await screen.findByRole('button', { name: 'Новый' })
    await waitFor(() => expect(create).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: 'Удалить Первый разговор' }))
    await userEvent.click(create)
    await act(async () => finishDelete())
    expect(screen.getByLabelText('Сообщение помощнику')).toBeDisabled()
    await act(async () => finishCreate(next))
    expect(screen.getByRole('heading', { name: 'Новый ремонт' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Новый ремонт' })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Первый разговор' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Сообщение помощнику')).toBeEnabled()
  })

  it('waits for the initial conversation list before accepting a message or creating a new conversation', async () => {
    const apiClient = client()
    let finishList!: (value: Awaited<ReturnType<AssistantApiClient['conversations']>>) => void
    let finishDetail!: (value: Awaited<ReturnType<AssistantApiClient['conversation']>>) => void
    const existing = { id: 'c-existing', title: 'Текущий ремонт', park_id: 4, issue_key: null, updated_at: '', messages: [], jobs: [] }
    vi.mocked(apiClient.conversations).mockImplementation(() => new Promise(resolve => { finishList = resolve }))
    vi.mocked(apiClient.conversation).mockImplementation(() => new Promise(resolve => { finishDetail = resolve }))
    renderPage(apiClient)
    const input = await screen.findByLabelText('Сообщение помощнику')
    expect(input).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Новый' })).toBeDisabled()
    fireEvent.submit(input.closest('form')!)
    expect(apiClient.createConversation).not.toHaveBeenCalled()
    expect(apiClient.sendMessage).not.toHaveBeenCalled()

    await act(async () => finishList([existing]))
    expect(input).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Отправить' })).toBeDisabled()
    await act(async () => finishDetail(existing))
    expect(input).toBeEnabled()
    await userEvent.type(input, 'Как проверить лидар?')
    expect(input).toHaveValue('Как проверить лидар?')
    expect(screen.getByRole('button', { name: 'Отправить' })).toBeEnabled()
    expect(apiClient.createConversation).not.toHaveBeenCalled()
  })

  it('keeps message creation unavailable after the initial conversation list fails and recovers on retry', async () => {
    const apiClient = client()
    vi.mocked(apiClient.conversations).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce([])
    renderPage(apiClient)
    await screen.findByText('Не удалось загрузить разговоры')
    const input = screen.getByLabelText('Сообщение помощнику')
    expect(input).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Новый' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Повторить' }))
    await waitFor(() => expect(input).toBeEnabled())
    expect(screen.getByRole('button', { name: 'Новый' })).toBeEnabled()
    expect(apiClient.createConversation).not.toHaveBeenCalled()
  })

  it('disables message submission while a newly selected conversation is loading', async () => {
    const apiClient = client()
    const first = { id: 'c-1', title: 'Первый разговор', park_id: 4, issue_key: null, updated_at: '', messages: [], jobs: [] }
    const second = { id: 'c-2', title: 'Второй разговор', park_id: 4, issue_key: null, updated_at: '', messages: [], jobs: [] }
    vi.mocked(apiClient.conversations).mockResolvedValue([first, second])
    vi.mocked(apiClient.conversation).mockImplementation(id => id === 'c-1' ? Promise.resolve(first) : new Promise(() => {}))
    renderPage(apiClient)
    await screen.findByRole('heading', { name: 'Первый разговор' })

    await userEvent.click(screen.getByRole('button', { name: 'Второй разговор' }))

    expect(screen.getByLabelText('Сообщение помощнику')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Отправить' })).toBeDisabled()
  })

  it('keeps the draft and pending job when polling reaches its time limit', async () => {
    const apiClient = client()
    vi.mocked(apiClient.job).mockResolvedValue({ id: 'j-1', kind: 'chat', state: 'running', created_at: '', updated_at: '', error: null, result: null })
    renderPage(apiClient)
    const input = await screen.findByLabelText('Сообщение помощнику')

    vi.useFakeTimers()
    fireEvent.change(input, { target: { value: 'Проверить питание' } })
    fireEvent.submit(input.closest('form')!)
    await act(async () => { await vi.runAllTimersAsync() })
    vi.useRealTimers()

    expect(input).toHaveValue('Проверить питание')
    expect(screen.getByRole('status')).toHaveTextContent('Ответ ещё готовится')
    expect(apiClient.job).toHaveBeenCalledTimes(180)
  })

  it('resumes and displays an unfinished conversation job after reload', async () => {
    const apiClient = client()
    const pending = { id: 'j-pending', kind: 'chat', state: 'running' as const, created_at: '', updated_at: '', error: null, result: null }
    vi.mocked(apiClient.conversations).mockResolvedValue([{ id: 'c-1', title: 'Диагностика', park_id: 4, issue_key: null, updated_at: '' }])
    vi.mocked(apiClient.conversation).mockResolvedValue({ id: 'c-1', title: 'Диагностика', park_id: 4, issue_key: null, updated_at: '', messages: [], jobs: [pending] })
    vi.mocked(apiClient.job).mockImplementation(() => new Promise(() => {}))

    renderPage(apiClient)

    expect(await screen.findByText(/Ответ ещё готовится/)).toBeVisible()
    expect(apiClient.job).toHaveBeenCalledWith('j-pending', expect.any(AbortSignal))
  })

  it('resumes an unfinished job again after switching away and reopening its conversation', async () => {
    const apiClient = client()
    const pending = { id: 'j-pending', kind: 'chat', state: 'running' as const, created_at: '', updated_at: '', error: null, result: null }
    const first = { id: 'c-1', title: 'Первый разговор', park_id: 4, issue_key: null, updated_at: '', messages: [], jobs: [pending] }
    const second = { ...first, id: 'c-2', title: 'Второй разговор', jobs: [] }
    vi.mocked(apiClient.conversations).mockResolvedValue([first, second])
    vi.mocked(apiClient.conversation).mockImplementation(async id => id === first.id ? first : second)
    vi.mocked(apiClient.job).mockImplementation(() => new Promise(() => {}))
    renderPage(apiClient)
    await waitFor(() => expect(apiClient.job).toHaveBeenCalledTimes(1))
    const firstSignal = vi.mocked(apiClient.job).mock.calls[0][1]!
    await userEvent.click(screen.getByRole('button', { name: 'Второй разговор' }))
    await screen.findByRole('heading', { name: 'Второй разговор' })
    expect(firstSignal.aborted).toBe(true)
    await userEvent.click(screen.getByRole('button', { name: 'Первый разговор' }))
    await waitFor(() => expect(apiClient.job).toHaveBeenCalledTimes(2))
    expect(vi.mocked(apiClient.job).mock.calls[1][1]!.aborted).toBe(false)
    expect(apiClient.cancelJob).not.toHaveBeenCalled()
  })

  it('stops UI job polling on unmount without cancelling the durable job', async () => {
    const apiClient = client()
    const pending = { id: 'j-pending', kind: 'chat', state: 'running' as const, created_at: '', updated_at: '', error: null, result: null }
    vi.mocked(apiClient.conversations).mockResolvedValue([{ id: 'c-1', title: 'Диагностика', park_id: 4, issue_key: null, updated_at: '' }])
    vi.mocked(apiClient.conversation).mockResolvedValue({ id: 'c-1', title: 'Диагностика', park_id: 4, issue_key: null, updated_at: '', messages: [], jobs: [pending] })
    vi.mocked(apiClient.job).mockImplementation(() => new Promise(() => {}))
    const view = renderPage(apiClient)
    await screen.findByText(/Ответ ещё готовится/)
    const signal = (vi.mocked(apiClient.job).mock.calls[0] as unknown[])[1] as AbortSignal

    view.unmount()

    expect(signal).toBeInstanceOf(AbortSignal)
    expect(signal.aborted).toBe(true)
    expect(apiClient.cancelJob).not.toHaveBeenCalled()
  })

  it('does not start polling when message creation resolves after unmount', async () => {
    const apiClient = client()
    let finishSend!: (job: Awaited<ReturnType<AssistantApiClient['sendMessage']>>) => void
    vi.mocked(apiClient.sendMessage).mockImplementation(() => new Promise(resolve => { finishSend = resolve }))
    const view = renderPage(apiClient)
    const input = await screen.findByLabelText('Сообщение помощнику')
    fireEvent.change(input, { target: { value: 'Проверить питание' } })
    fireEvent.submit(input.closest('form')!)
    await waitFor(() => expect(apiClient.sendMessage).toHaveBeenCalledTimes(1))

    view.unmount()
    await act(async () => finishSend({ id: 'j-late', kind: 'chat', state: 'queued', created_at: '', updated_at: '', error: null, result: null }))

    expect(apiClient.job).not.toHaveBeenCalled()
    expect(apiClient.cancelJob).not.toHaveBeenCalled()
  })

  it.each([
    ['ai_context_too_large', 'Уменьшите запрос'],
    ['ai_sources_changed', 'Источники изменились'],
    ['ai_queue_full', 'Очередь помощника заполнена'],
    ['ai_finalize_busy', 'Не удалось сохранить результат'],
    ['issue_park_mismatch', 'Задача относится к другому парку'],
    ['script_test_required', 'Сначала проверьте текущую ревизию скрипта'],
    ['connector_url_invalid', 'Проверьте HTTPS-адрес подключения'],
  ])('shows a friendly message for %s', (code, expected) => {
    expect(assistantErrorText(new Error(code))).toContain(expected)
  })

  it('prevents duplicate imports and disables import controls while a batch is uploading', async () => {
    const apiClient = client({ ...ready, can_manage: true })
    let finish!: (value: { created: number; duplicates: number; rejected: number }) => void
    vi.mocked(apiClient.importDocuments).mockImplementation(() => new Promise(resolve => { finish = resolve }))
    renderPage(apiClient, admin)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('tab', { name: 'База знаний' }))
    const file = new File([JSON.stringify([{ title: 'Инструкция', content: 'Текст', kind: 'manual', source_ref: 'seed:1' }])], 'seed.json', { type: 'application/json' })
    const picker = screen.getByLabelText('Файл базы знаний')
    await user.upload(picker, file)
    const upload = await screen.findByRole('button', { name: 'Импортировать' })

    await user.click(upload)
    await user.click(upload)

    expect(apiClient.importDocuments).toHaveBeenCalledTimes(1)
    expect(picker).toBeDisabled()
    expect(screen.getByLabelText('Сразу активировать инструкции')).toBeDisabled()
    await act(async () => finish({ created: 1, duplicates: 0, rejected: 0 }))
  })

  it('limits import batches by count and UTF-8 JSON size', () => {
    const documents = Array.from({ length: 101 }, (_, index) => ({
      title: `Документ ${index}`, content: 'я'.repeat(45_000), kind: 'manual' as const, source_ref: `seed:${index}`,
    }))

    const batches = createKnowledgeImportBatches(documents, 4, true, true)

    expect(batches.length).toBeGreaterThan(1)
    for (const batch of batches) {
      expect(batch.length).toBeGreaterThan(0)
      expect(batch.length).toBeLessThanOrEqual(100)
      expect(new TextEncoder().encode(JSON.stringify({ documents: batch, park_id: 4, activate_manuals: true, activate_unverified: true })).byteLength).toBeLessThanOrEqual(4 * 1024 * 1024)
    }
  })

  it('asks for confirmation before deleting a script', async () => {
    const apiClient = client({ ...ready, can_manage: true })
    vi.mocked(apiClient.scripts).mockResolvedValue([{ id: 's-1', name: 'Диагностика', source: 'def main(data): return data', revision: 2, enabled: false, tested_revision: 2, updated_at: '' }])
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    renderPage(apiClient, admin)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('tab', { name: 'Скрипты' }))
    await user.click(await screen.findByRole('button', { name: /Диагностика/ }))

    await user.click(screen.getByRole('button', { name: 'Удалить' }))

    expect(confirm).toHaveBeenCalledWith('Удалить скрипт «Диагностика»?')
    expect(apiClient.deleteScript).not.toHaveBeenCalled()
    confirm.mockRestore()
  })

  it('shows the issue path template and confirms connector deletion', async () => {
    const apiClient = client({ ...ready, can_manage: true })
    vi.mocked(apiClient.connectors).mockResolvedValue([{ id: 'c-1', name: 'Сервис', url: 'https://service.example/issues/{{issue_key}}', method: 'PATCH', enabled: true, token_set: true, revision: 1 }])
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    renderPage(apiClient, admin)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('tab', { name: 'Подключения' }))
    await user.click(await screen.findByRole('button', { name: /Сервис/ }))

    expect(screen.getByLabelText('HTTPS адрес')).toHaveValue('https://service.example/issues/{{issue_key}}')
    await user.click(screen.getByRole('button', { name: 'Удалить' }))
    expect(confirm).toHaveBeenCalledWith('Удалить подключение «Сервис»?')
    expect(apiClient.deleteConnector).not.toHaveBeenCalled()
    confirm.mockRestore()
  })

  it('reports separate history cleanup counts including completed script jobs', async () => {
    const apiClient = client({ ...ready, can_manage: true })
    vi.mocked(apiClient.config).mockResolvedValue({ enabled: true, learning_enabled: true, revision: 1 })
    vi.mocked(apiClient.prompts).mockResolvedValue([])
    vi.mocked(apiClient.runs).mockResolvedValue([])
    vi.mocked(apiClient.maintenance).mockResolvedValue({ deleted: 1, conversations_deleted: 1, jobs_deleted: 5, messages_deleted: 2 })
    renderPage(apiClient, admin)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('tab', { name: 'Настройки и журнал' }))
    await user.click(screen.getByText('Очистка журналов'))
    await user.click(screen.getByRole('button', { name: 'Очистить историю старше 30 дней' }))

    expect(apiClient.maintenance).toHaveBeenCalledWith('history', 30)
    expect(await screen.findByText('Удалено бесед: 1; заданий: 5; сообщений: 2')).toBeVisible()
  })

  it('guards runtime actions, confirms model removal, and reports cleanup failures', async () => {
    const apiClient = client({ ...ready, can_manage: true })
    vi.mocked(apiClient.config).mockResolvedValue({ enabled: true, learning_enabled: true, revision: 1 })
    vi.mocked(apiClient.prompts).mockResolvedValue([])
    vi.mocked(apiClient.runs).mockResolvedValue([])
    let finishRuntime!: (status: AiStatus) => void
    vi.mocked(apiClient.runtime).mockImplementation(() => new Promise(resolve => { finishRuntime = resolve }))
    vi.mocked(apiClient.purgeRuns).mockRejectedValue(new Error('ai_queue_full'))
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    renderPage(apiClient, admin)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('tab', { name: 'Настройки и журнал' }))
    await user.click(screen.getByText('Управление runtime'))

    await user.click(screen.getByRole('button', { name: 'Установить' }))
    expect(apiClient.runtime).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Включить' })).toBeDisabled()
    await act(async () => finishRuntime({ ...ready, can_manage: true, ready: false, reason: 'installing' }))
    await user.click(screen.getByRole('button', { name: 'Удалить модель' }))
    expect(confirm).toHaveBeenCalled()
    expect(apiClient.runtime).toHaveBeenCalledTimes(1)

    await user.click(screen.getByText('Очистка журналов'))
    await user.click(screen.getByRole('button', { name: 'Очистить запуски старше 30 дней' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Очередь помощника заполнена')
    confirm.mockRestore()
  })
})
