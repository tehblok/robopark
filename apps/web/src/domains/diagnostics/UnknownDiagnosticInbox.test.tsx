import { StrictMode } from 'react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { type DiagnosticRule, type User } from '../../api'
import { AuthContext } from '../../auth-context'
import { resourceStore } from '../../lib/resource'
import { DiagnosticRuleEditor } from './DiagnosticRuleEditor'
import { unknownDiagnosticApi, type UnknownDiagnostic } from './unknownDiagnosticApi'

const user: User = { id: 1, username: 'admin', role: 'admin', access_status: 'approved', parks: [], permissions: [] }
const unknown: UnknownDiagnostic = { id: 7, source_path: 'errors.0', pattern: '{"a": 1, "b": 2}', raw_value: { b: 2, a: 1 }, original_value: { b: 2, a: 1 }, observations: 19, last_robot: 'R42', first_seen_at: '2026-09-01T10:00:00Z', last_seen_at: '2026-09-06T10:00:00Z', state: 'new', rule_id: null }
const first: DiagnosticRule = { id: 1, source_path: 'errors', match_kind: 'exact', pattern: 'E01', example: 'E01', title: 'Лидар', description: 'Проверить лидар', severity: 'critical', part: 'Лидар', preferred_view: 'front', x: .25, y: .6, indicator: 'point', is_enabled: true, sort_order: 0 }
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json', ETag: '"v1"' } })
let items: UnknownDiagnostic[]
let rules: DiagnosticRule[]
let handler: (path: string, init: RequestInit) => Response | Promise<Response>
const requests: { path: string; init: RequestInit }[] = []
const tree = (principal = user) => <MemoryRouter initialEntries={['/?park=7&rule=1']}><AuthContext.Provider value={{ user: principal, loading: false, login: async () => principal, refreshUser: async () => principal, logout: async () => undefined }}><DiagnosticRuleEditor /></AuthContext.Provider></MemoryRouter>
const inbox = () => within(screen.getByRole('tabpanel', { name: 'Неизвестные ошибки' }))
const catalog = () => within(screen.getByRole('tabpanel', { name: 'Каталог ошибок' }))
async function openInbox() {
  await screen.findByRole('tab', { name: 'Неизвестные ошибки' })
  fireEvent.click(screen.getByRole('tab', { name: 'Неизвестные ошибки' }))
  fireEvent.click(await inbox().findByRole('button', { name: /Наблюдений: 19/ }))
}
async function startDraft() {
  await openInbox()
  fireEvent.click(inbox().getByRole('button', { name: 'Разметить' }))
  for (const [label, value] of [['Название ошибки', 'Сбой привода'], ['Часть робота', 'Привод'], ['Расшифровка', 'Проверить подключение'], ['Ракурс', 'rear'], ['Координата X', '.3'], ['Координата Y', '.7']]) fireEvent.change(inbox().getByLabelText(label), { target: { value } })
}
beforeEach(() => {
  resourceStore.clearAll(); requests.length = 0; items = [{ ...unknown }]; rules = [{ ...first }]
  handler = (path, init) => {
    if (path.startsWith('/api/admin/diagnostic-unknowns?')) {
      const params = new URL(path, 'http://local').searchParams
      const matches = items.filter(item => item.state === params.get('state'))
      const offset = Number(params.get('offset'))
      return json({ items: matches.slice(offset, offset + 50), total: matches.length, offset, limit: 50, has_more: matches.length > offset + 50 })
    }
    if (path.endsWith('/classify')) {
      const rule = { ...JSON.parse(String(init.body)).rule, id: 3, sort_order: 1 }
      rules.push(rule); items = items.map(item => ({ ...item, state: 'mapped', rule_id: 3 }))
      return json(rule, 201)
    }
    if (path.endsWith('/ignore') || path.endsWith('/reopen')) { items = items.map(item => ({ ...item, state: path.endsWith('/ignore') ? 'ignored' : 'new' })); return json(items[0]) }
    if (path.endsWith('/preview')) return json({ matched: true, events: [] })
    if (init.method === 'PATCH') { const rule = { ...rules.at(-1)!, ...JSON.parse(String(init.body)) }; rules[rules.length - 1] = rule; return json(rule) }
    return json(rules)
  }
  vi.stubGlobal('fetch', vi.fn((path: string, init: RequestInit = {}) => { requests.push({ path, init }); return Promise.resolve(handler(path, init)) }))
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); resourceStore.clearAll() })

it('loads only when opened, groups evidence and classifies through the existing rule form with exact backend pattern', async () => {
  render(tree())
  await screen.findByLabelText('Название ошибки')
  expect(requests.some(request => request.path.includes('diagnostic-unknowns'))).toBe(false)
  await openInbox()
  expect(inbox().getAllByText(/Последний робот: R42/)).toHaveLength(2)
  fireEvent.click(inbox().getByRole('button', { name: 'Разметить' }))
  expect(inbox().getByLabelText('Путь источника')).toHaveValue('errors.0')
  expect(inbox().getByLabelText('Код или шаблон')).toHaveValue(unknown.pattern)
  expect(inbox().getByLabelText('Путь источника')).toHaveAttribute('readonly')
  expect(inbox().getByLabelText('Пример входного значения')).toHaveValue(JSON.stringify(unknown.raw_value))
  expect(inbox().getByLabelText('Координата X')).toHaveValue(null)
  expect(inbox().queryByLabelText('Маркер: часть робота')).not.toBeInTheDocument()
  for (const [label, value] of [['Название ошибки', 'Сбой привода'], ['Часть робота', 'Привод'], ['Расшифровка', 'Проверить подключение']]) fireEvent.change(inbox().getByLabelText(label), { target: { value } })
  expect(inbox().getByRole('button', { name: 'Сохранить правило' })).toBeDisabled()
  fireEvent.change(inbox().getByLabelText('Ракурс'), { target: { value: 'rear' } })
  fireEvent.change(inbox().getByLabelText('Координата X'), { target: { value: '.3' } })
  fireEvent.change(inbox().getByLabelText('Координата Y'), { target: { value: '.7' } })
  fireEvent.click(inbox().getByRole('button', { name: 'Проверить пример' }))
  await inbox().findByText('Совпадение найдено')
  fireEvent.click(inbox().getByRole('button', { name: 'Сохранить правило' }))
  await waitFor(() => expect(screen.getByRole('tab', { name: 'Каталог ошибок' })).toHaveAttribute('aria-selected', 'true'))
  await waitFor(() => expect(catalog().getByLabelText('Название ошибки')).toHaveValue('Сбой привода'))
  const classified = requests.find(request => request.path.endsWith('/7/classify'))!
  expect(JSON.parse(String(classified.init.body))).toEqual({ rule: expect.objectContaining({ pattern: unknown.pattern, source_path: unknown.source_path, preferred_view: 'rear', x: .3, y: .7 }) })
  expect(requests.some(request => request.path === '/api/admin/diagnostic-rules' && request.init.method === 'POST')).toBe(false)
  expect(Object.keys(localStorage).some(key => key.includes('unknowns'))).toBe(false)
})

it('keeps both drafts through tab changes and refreshes evidence without resetting annotation', async () => {
  render(tree())
  fireEvent.change(await screen.findByLabelText('Название ошибки'), { target: { value: 'Черновик каталога' } })
  await startDraft()
  fireEvent.click(screen.getByRole('tab', { name: 'Каталог ошибок' }))
  expect(catalog().getByLabelText('Название ошибки')).toHaveValue('Черновик каталога')
  fireEvent.click(screen.getByRole('tab', { name: 'Неизвестные ошибки' }))
  items[0] = { ...unknown, observations: 24 }
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 120_001)
  fireEvent.focus(window)
  await inbox().findByRole('button', { name: /Наблюдений: 24/ })
  expect(inbox().getByLabelText('Название ошибки')).toHaveValue('Сбой привода')
  expect(inbox().getByLabelText('Координата Y')).toHaveValue(.7)
})

it('ignores an error until it is restored', async () => {
  render(tree()); await openInbox()
  fireEvent.click(inbox().getByRole('button', { name: 'Игнорировать' }))
  await inbox().findByText('Ошибка скрыта из проверок робота до восстановления.')
  fireEvent.click(inbox().getByRole('tab', { name: 'Игнорируемые' }))
  await inbox().findByRole('button', { name: /Наблюдений: 19/ })
  fireEvent.click(inbox().getByRole('button', { name: 'Вернуть' }))
  await inbox().findByText('Ошибка возвращена в проверки робота.')
  expect(requests.filter(request => request.init.method === 'POST').map(request => request.path)).toEqual(['/api/admin/diagnostic-unknowns/7/ignore', '/api/admin/diagnostic-unknowns/7/reopen'])
})

it.each([401, 403])('clears protected forms and stops polling after %s from the inbox', async status => {
  const normal = handler
  handler = (path, init) => path.includes('diagnostic-unknowns') ? json({ detail: 'PRIVATE_RESPONSE' }, status) : normal(path, init)
  render(tree()); await screen.findByLabelText('Название ошибки')
  fireEvent.click(screen.getByRole('tab', { name: 'Неизвестные ошибки' }))
  await screen.findByText('Каталог недоступен')
  expect(screen.queryByLabelText('Название ошибки')).not.toBeInTheDocument()
  expect(screen.queryByText('PRIVATE_RESPONSE')).not.toBeInTheDocument()
  const count = requests.length
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 120_001); fireEvent.focus(window)
  await act(async () => undefined)
  expect(requests.slice(count).map(request => request.path)).toEqual([])
})

it('does not expose the inbox to non-admin roles', async () => {
  render(tree({ ...user, role: 'operator' }))
  expect(screen.queryByRole('tab', { name: 'Неизвестные ошибки' })).not.toBeInTheDocument()
  expect(requests).toHaveLength(0)
})

it('paginates grouped errors and opens an existing mapped rule', async () => {
  items = Array.from({ length: 51 }, (_, index) => ({ ...unknown, id: index + 1, pattern: `E${index + 1}` }))
  render(tree()); fireEvent.click(screen.getByRole('tab', { name: 'Неизвестные ошибки' }))
  fireEvent.click(await inbox().findByRole('button', { name: 'Далее' }))
  await inbox().findByText('51–51 из 51')
  expect(requests.some(request => request.path.endsWith('limit=50&offset=50'))).toBe(true)
  items = [{ ...unknown, state: 'mapped', rule_id: 1 }]
  fireEvent.click(inbox().getByRole('tab', { name: 'Размеченные' }))
  fireEvent.click(await inbox().findByRole('button', { name: /Наблюдений: 19/ }))
  fireEvent.click(inbox().getByRole('button', { name: 'Открыть правило №1' }))
  expect(catalog().getByLabelText('Название ошибки')).toHaveValue('Лидар')
})

it('retires a StrictMode-aborted inbox load before the replacement mount', async () => {
  const load = vi.spyOn(unknownDiagnosticApi, 'list').mockImplementationOnce((_state, _offset, signal) => new Promise((_, reject) => signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError'))))).mockResolvedValue({ items: [unknown], total: 1, limit: 50, offset: 0, has_more: false })
  render(<StrictMode>{tree()}</StrictMode>)
  fireEvent.click(screen.getByRole('tab', { name: 'Неизвестные ошибки' }))
  await inbox().findByRole('button', { name: /Наблюдений: 19/ })
  expect(load).toHaveBeenCalledTimes(2)
})

it('adopts a committed rule instead of repeating classification after switching tabs while saving', async () => {
  let complete!: (response: Response) => void
  const normal = handler
  handler = (path, init) => path.endsWith('/classify') ? new Promise(resolve => { complete = resolve }) : normal(path, init)
  render(tree()); await startDraft()
  fireEvent.click(inbox().getByRole('button', { name: 'Сохранить правило' }))
  fireEvent.change(inbox().getByLabelText('Название ошибки'), { target: { value: 'Поздний черновик' } })
  fireEvent.click(screen.getByRole('tab', { name: 'Каталог ошибок' }))
  const saved = { ...first, ...JSON.parse(String(requests.find(request => request.path.endsWith('/classify'))!.init.body)).rule, id: 3 }; rules.push(saved)
  await act(async () => complete(json(saved, 201)))
  expect(screen.getByRole('tab', { name: 'Каталог ошибок' })).toHaveAttribute('aria-selected', 'true')
  fireEvent.click(screen.getByRole('tab', { name: 'Неизвестные ошибки' }))
  expect(inbox().getByLabelText('Название ошибки')).toHaveValue('Поздний черновик')
  fireEvent.click(inbox().getByRole('button', { name: 'Сохранить правило' }))
  await inbox().findByText('Правило сохранено.')
  expect(requests.filter(request => request.path.endsWith('/classify'))).toHaveLength(1)
  expect(requests.some(request => request.path === '/api/admin/diagnostic-rules/3' && request.init.method === 'PATCH')).toBe(true)
})

it('transfers edits made during classification to the created catalogue rule', async () => {
  let complete!: (response: Response) => void
  const normal = handler
  handler = (path, init) => path.endsWith('/classify') ? new Promise(resolve => { complete = resolve }) : normal(path, init)
  render(tree()); await startDraft()
  fireEvent.click(inbox().getByRole('button', { name: 'Сохранить правило' }))
  fireEvent.change(inbox().getByLabelText('Название ошибки'), { target: { value: 'Редакция после отправки' } })
  const saved = { ...first, ...JSON.parse(String(requests.find(request => request.path.endsWith('/classify'))!.init.body)).rule, id: 3 }; rules.push(saved)
  await act(async () => complete(json(saved, 201)))
  await waitFor(() => expect(catalog().getByLabelText('Название ошибки')).toHaveValue('Редакция после отправки'))
  fireEvent.click(catalog().getByRole('button', { name: 'Сохранить правило' }))
  await catalog().findByText('Правило сохранено.')
  expect(rules.at(-1)?.title).toBe('Редакция после отправки')
  expect(requests.filter(request => request.path.endsWith('/classify'))).toHaveLength(1)
})

it('allows a short regex for a long unknown value while keeping its source evidence', async () => {
  items = [{ ...unknown, pattern: 'E'.repeat(700), raw_value: 'E'.repeat(700), original_value: 'E'.repeat(700) }]
  render(tree()); await openInbox()
  fireEvent.click(inbox().getByRole('button', { name: 'Разметить' }))
  expect(inbox().getByText(/Значение длиннее 512 символов/)).toBeVisible()
  fireEvent.change(inbox().getByLabelText('Сопоставление'), { target: { value: 'regex' } })
  fireEvent.change(inbox().getByLabelText('Код или шаблон'), { target: { value: '^E+$' } })
  expect(inbox().queryByText(/Значение длиннее 512 символов/)).not.toBeInTheDocument()
  expect(inbox().getByLabelText('Пример входного значения')).toHaveValue(JSON.stringify('E'.repeat(700)))
})

it('marks long raw diagnostics as bounded content and uses the shared selection action', async () => {
  const longValue = `FrequencyBelow:${'7'.repeat(70)}`
  items = [{ ...unknown, source_path: `telemetry.${'nested.'.repeat(10)}frequency`, pattern: longValue, raw_value: { signal: longValue } }]
  render(tree()); await openInbox()

  expect(inbox().getByRole('button', { name: /Наблюдений: 19/ })).toHaveClass('rp-button')
  expect(inbox().getByText(/FrequencyBelow/, { selector: 'pre' })).toHaveClass('rp-diagnostic-raw')
})

it('constrains the diagnostic master-detail columns and raw payloads', () => {
  const source = readFileSync(resolve('src/domains/diagnostics/diagnostics.css'), 'utf8')

  expect(source).toMatch(/grid-template-columns:\s*minmax\(16rem,\s*28rem\)\s+minmax\(0,\s*1fr\)/)
  expect(source).toMatch(/@media \(max-width:\s*1199px\)[\s\S]*\.rp-diagnostic-editor \.rp-master-detail\s*\{[\s\S]*grid-template-columns:\s*minmax\(0,\s*1fr\)/)
  expect(source).toMatch(/\.rp-diagnostic-raw\s*\{[^}]*white-space:\s*pre-wrap[^}]*overflow-wrap:\s*anywhere[^}]*max-inline-size:\s*100%[^}]*overflow:\s*auto/)
})

it('recovers an already classified error without losing the draft or repeating POST', async () => {
  const normal = handler
  handler = (path, init) => path.endsWith('/classify') ? (() => { items = [{ ...unknown, state: 'mapped', rule_id: 1 }]; return json({ detail: 'diagnostic_unknown_already_mapped' }, 409) })() : path === '/api/admin/diagnostic-unknowns/7' ? json({ ...unknown, state: 'mapped', rule_id: 1 }) : normal(path, init)
  render(tree()); await startDraft()
  fireEvent.click(inbox().getByRole('button', { name: 'Сохранить правило' }))
  await inbox().findByRole('button', { name: 'Открыть правило №1' })
  expect(inbox().getByLabelText('Название ошибки')).toHaveValue('Сбой привода')
  fireEvent.click(inbox().getByRole('button', { name: 'Сохранить правило' }))
  await act(async () => undefined)
  expect(requests.filter(request => request.path.endsWith('/classify'))).toHaveLength(1)
  fireEvent.click(inbox().getByRole('button', { name: 'Открыть правило №1' }))
  expect(catalog().getByLabelText('Название ошибки')).toHaveValue('Лидар')
})

it('previews the original diagnostic unit instead of a residual object missing known fields', async () => {
  items = [{ ...unknown, raw_value: { b: 2 }, original_value: { a: 1, b: 2 } }]
  render(tree()); await startDraft()
  expect(inbox().getByLabelText('Пример входного значения')).toHaveValue('{"a":1,"b":2}')
  expect(inbox().getByLabelText('Код или шаблон')).toHaveValue(unknown.pattern)
  fireEvent.click(inbox().getByRole('button', { name: 'Проверить пример' }))
  await inbox().findByText('Совпадение найдено')
  const preview = requests.find(request => request.path.endsWith('/preview'))!
  expect(JSON.parse(String(preview.init.body)).rule.example).toBe('{"a":1,"b":2}')
})

it('asks for a fresh observation before classifying a legacy sample without its original value', async () => {
  items = [{ ...unknown, original_value: null }]
  render(tree()); await openInbox()
  expect(inbox().getByRole('button', { name: 'Разметить' })).toBeDisabled()
  expect(inbox().getByText('Повторите проверку робота для получения исходного сигнала.')).toBeVisible()
})

it.each([
  ['unknown_rule_does_not_match', 'Правило не распознаёт исходный сигнал. Проверьте шаблон и повторите проверку примера.'],
  ['unknown_sample_requires_observation', 'Повторите проверку робота для получения исходного сигнала.'],
])('explains the classification rejection %s and retains annotation', async (detail, message) => {
  const normal = handler
  handler = (path, init) => path.endsWith('/classify') ? json({ detail }, 422) : normal(path, init)
  render(tree()); await startDraft()
  fireEvent.click(inbox().getByRole('button', { name: 'Сохранить правило' }))
  await inbox().findByText(message)
  expect(inbox().getByLabelText('Название ошибки')).toHaveValue('Сбой привода')
})

it('rejects a queued focus callback from an owner retired by access denial before starting fetch', async () => {
  const listeners = vi.spyOn(window, 'addEventListener')
  const normal = handler
  handler = (path, init) => path.includes('diagnostic-unknowns') ? json({}, 403) : normal(path, init)
  render(tree()); await screen.findByLabelText('Название ошибки')
  fireEvent.click(screen.getByRole('tab', { name: 'Неизвестные ошибки' }))
  await screen.findByText('Каталог недоступен')
  const retiredFocusCallbacks = listeners.mock.calls.filter(([name]) => name === 'focus').map(([, callback]) => callback)
  const count = requests.length
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 120_001)
  await act(async () => {
    // Model a callback already queued when passive effect cleanup retires its
    // subscription. Its captured enabled flag still belongs to the old render.
    for (const callback of retiredFocusCallbacks) if (typeof callback === 'function') callback.call(window, new Event('focus'))
  })
  expect(requests.slice(count).map(request => request.path)).toEqual([])
})

// Keep lifecycle assertions deterministic; pollingCapacity tests exercise jitter.
beforeEach(() => { vi.spyOn(Math, 'random').mockReturnValue(0) })
