import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { User } from '../../api'
import { AuthContext } from '../../auth-context'
import { ParkScopeContext } from '../../app/park/parkScope'
import { AssistantPage } from './AssistantPage'
import { assistantErrorText, createKnowledgeImportBatches } from './assistantUi'
import type { AssistantApiClient, AiStatus } from './assistantApi'

const park = { id: 4, name: 'Север', tag: 'NORTH', timezone: 'Europe/Moscow' }
const operator: User = { id: 1, username: 'operator', role: 'operator', access_status: 'approved', permissions: ['nav.tasks'], parks: [park] }
const admin: User = { ...operator, id: 2, username: 'admin', role: 'admin' }
const ready: AiStatus = {
  supported: true, installed: true, enabled: true, ready: true, reason: null,
  model: 'prism-ml/Ternary-Bonsai-2-27B-gguf', backend: 'cuda', can_manage: false,
  counts: { documents: 2, candidates: 1, jobs: 0 },
}

function client(status: AiStatus = ready): AssistantApiClient {
  return {
    status: vi.fn().mockResolvedValue(status),
    conversations: vi.fn().mockResolvedValue([]),
    conversation: vi.fn(), createConversation: vi.fn().mockResolvedValue({ id: 'c-1', title: 'Новый разговор', park_id: 4, issue_key: null, updated_at: '2026-10-04T10:00:00Z' }),
    deleteConversation: vi.fn(), sendMessage: vi.fn().mockResolvedValue({ id: 'j-1', kind: 'chat', state: 'queued', created_at: '', updated_at: '', error: null, result: null }),
    job: vi.fn().mockResolvedValue({ id: 'j-1', kind: 'chat', state: 'succeeded', created_at: '', updated_at: '', error: null, result: {} }), cancelJob: vi.fn(),
    documents: vi.fn().mockResolvedValue({ items: [], total: 0, offset: 0, limit: 30 }), document: vi.fn(), createDocument: vi.fn(), updateDocument: vi.fn(), deleteDocument: vi.fn(), importDocuments: vi.fn(),
    config: vi.fn(), updateConfig: vi.fn(), runtime: vi.fn(), prompts: vi.fn(), updatePrompt: vi.fn(),
    connectors: vi.fn(), createConnector: vi.fn(), updateConnector: vi.fn(), deleteConnector: vi.fn(),
    scripts: vi.fn(), createScript: vi.fn(), updateScript: vi.fn(), deleteScript: vi.fn(), testScript: vi.fn(),
    createDraft: vi.fn(), automations: vi.fn(), createAutomation: vi.fn(), updateAutomation: vi.fn(), deleteAutomation: vi.fn(), previewAutomation: vi.fn(),
    runs: vi.fn(), purgeRuns: vi.fn(), maintenance: vi.fn(),
  }
}

function renderPage(apiClient: AssistantApiClient, user: User = operator, path = '/assistant') {
  return render(<MemoryRouter initialEntries={[path]}><AuthContext.Provider value={{
    user, loading: false, login: vi.fn(), refreshUser: vi.fn(), logout: vi.fn(),
  }}><ParkScopeContext.Provider value={{
    parkId: 4, selectedPark: park, parks: [park], loading: false, locked: false,
    setParkId: vi.fn(), refreshParks: vi.fn(),
  }}><AssistantPage apiClient={apiClient} /></ParkScopeContext.Provider></AuthContext.Provider></MemoryRouter>)
}

describe('AssistantPage', () => {
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

  it('polls supported status and reflects readiness changes', async () => {
    const apiClient = client()
    const starting = { ...ready, ready: false, reason: 'starting' }
    const disabled = { ...ready, ready: false, enabled: false, reason: 'disabled' }
    vi.mocked(apiClient.status).mockResolvedValueOnce(starting).mockResolvedValueOnce(ready).mockResolvedValue(disabled)
    let tick!: () => Promise<void>
    const interval = vi.spyOn(window, 'setInterval').mockImplementation((handler, delay) => { if (delay === 5000) tick = handler as () => Promise<void>; return 123 as unknown as ReturnType<typeof setInterval> })
    renderPage(apiClient)
    expect(await screen.findByText('Запускаем локальную модель.')).toBeVisible()

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

    let firstPoll!: Promise<void>
    await act(async () => { firstPoll = tick(); await tick() })
    expect(apiClient.status).toHaveBeenCalledTimes(2)
    await act(async () => { finishPoll(ready); await firstPoll })
    interval.mockRestore()
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
    await user.type(input, 'Как проверить лидар?')
    await user.click(screen.getByRole('button', { name: 'Отправить' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось отправить')
    expect(input).toHaveValue('Как проверить лидар?')

    await user.click(screen.getByRole('button', { name: 'Отправить' }))
    await waitFor(() => expect(apiClient.conversation).toHaveBeenCalledWith('c-1'))
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

    await waitFor(() => expect(apiClient.conversation).toHaveBeenCalledWith('c-b'))
    expect(apiClient.conversation).not.toHaveBeenCalledWith('c-a')
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
    expect(apiClient.job).toHaveBeenCalledWith('j-pending')
  })

  it.each([
    ['ai_context_too_large', 'Уменьшите запрос'],
    ['ai_sources_changed', 'Источники изменились'],
    ['ai_queue_full', 'Очередь помощника заполнена'],
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
