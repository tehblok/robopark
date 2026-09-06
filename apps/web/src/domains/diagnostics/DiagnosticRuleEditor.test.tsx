import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { type User } from '../../api'
import { AuthContext } from '../../auth-context'
import { resourceStore } from '../../lib/resource'
import { AdminEmergencyConfig } from '../../pages/AdminEmergencyConfig'

const user: User = { id: 1, username: 'admin', role: 'admin', access_status: 'approved', parks: [], permissions: ['nav.admin.emergency'] }
const first = { id: 1, source_path: 'errors', match_kind: 'exact', pattern: 'E01', example: 'E01', title: 'Лидар', description: 'Проверить лидар', severity: 'critical', part: 'Лидар', preferred_view: 'front', x: .25, y: .6, indicator: 'point', is_enabled: true, sort_order: 0 }
const second = { ...first, id: 2, title: 'Колесо', part: 'Колесо', pattern: 'E02', preferred_view: 'top', is_enabled: false, sort_order: 1 }
const event = { id: 'rule:0', rule_id: 0, source_path: 'errors', source_segments: ['errors'], raw_value: 'E01', title: 'Лидар', description: 'Проверить лидар', severity: 'critical', sort_order: 0, part: 'Лидар', view: 'front', x: .25, y: .6, indicator: 'point' }
let catalog = [first, second]
let etag = '"v1"'
type Handler = (path: string, init: RequestInit) => Promise<Response> | Response
let handler: Handler
const requests: { path: string; init: RequestInit }[] = []
const json = (body: unknown, status = 200, headers = {}) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } })
function Probe() { const location = useLocation(); const navigate = useNavigate(); return <><output aria-label="Адрес">{location.search}</output><button onClick={() => navigate('?park=8&tab=indication&rule=1')}>Другой парк</button></> }
function tree(principal = user, entry = '/admin/emergency/config?park=7&tab=indication&rule=1') {
  return <MemoryRouter initialEntries={[entry]}><AuthContext.Provider value={{ user: principal, loading: false, login: async () => principal, refreshUser: async () => principal, logout: async () => undefined }}><AdminEmergencyConfig /><Probe /></AuthContext.Provider></MemoryRouter>
}
beforeEach(() => {
  resourceStore.clearAll(); requests.length = 0; catalog = [first, second]; etag = '"v1"'
  handler = (path, init) => {
    if (path === '/api/admin/emergency/sections') return json([{ id: 'state', title: 'Состояние', is_enabled: true, roles: ['admin'], sort_order: 0, fields: [{ id: 9, path: 'data.status', label: 'Статус', sort_order: 0 }] }])
    if (path.endsWith('/preview')) return json({ matched: true, events: [event] })
    if (path === '/api/admin/diagnostic-rules' && init.method === 'POST') { const created = { ...first, ...JSON.parse(String(init.body)), id: 3 }; catalog = [...catalog, created]; return json(created, 201) }
    if (init.method === 'PATCH') { catalog = catalog.map(rule => rule.id === 1 ? { ...rule, ...JSON.parse(String(init.body)) } : rule); return json(catalog[0]) }
    if (path.endsWith('/disable')) { catalog = catalog.map(rule => rule.id === 1 ? { ...rule, is_enabled: false } : rule); etag = '"disabled"'; return json(catalog[0]) }
    if (path.endsWith('/reorder')) { catalog = [...catalog].reverse(); etag = '"v2"'; return json(catalog, 200, { ETag: etag }) }
    return json(catalog, 200, { ETag: etag })
  }
  vi.stubGlobal('fetch', vi.fn((input: string, init: RequestInit = {}) => { requests.push({ path: input, init }); return Promise.resolve(handler(input, init)) }))
  // jsdom has no PointerEvent; MouseEvent preserves the coordinates consumed by React.
  vi.stubGlobal('PointerEvent', MouseEvent)
})
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); resourceStore.clearAll() })

it('places and clamps a marker against the rendered image, supports numeric coordinates, and restores after reload', async () => {
  const mounted = render(tree())
  const image = await screen.findByRole('img', { name: 'Вид спереди' })
  vi.spyOn(image, 'getBoundingClientRect').mockReturnValue({ left: 100, top: 20, width: 200, height: 400 } as DOMRect)
  fireEvent.pointerUp(image, { clientX: 150, clientY: 320, pointerType: 'touch' })
  expect(screen.getByLabelText('Координата X')).toHaveValue(.25)
  expect(screen.getByLabelText('Координата Y')).toHaveValue(.75)
  fireEvent.pointerUp(image, { clientX: -30, clientY: 900 })
  expect(screen.getByLabelText('Координата X')).toHaveValue(0)
  expect(screen.getByLabelText('Координата Y')).toHaveValue(1)
  fireEvent.change(screen.getByLabelText('Координата X'), { target: { value: '.4' } })
  fireEvent.change(screen.getByLabelText('Координата Y'), { target: { value: '.3' } })
  expect(screen.getByLabelText('Маркер: Лидар')).toHaveStyle({ left: '40%', top: '30%' })
  fireEvent.change(screen.getByLabelText('Ракурс'), { target: { value: 'rear' } })
  expect(screen.getByRole('img', { name: 'Вид сзади' })).toHaveAttribute('width', '1454')
  fireEvent(window, new Event('resize'))
  expect(screen.getByLabelText('Маркер: Лидар')).toHaveStyle({ left: '40%', top: '30%' })
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить правило' }))
  await screen.findByText('Правило сохранено.')
  const patch = requests.find(request => request.init.method === 'PATCH')!
  expect(JSON.parse(String(patch.init.body))).toMatchObject({ x: .4, y: .3, preferred_view: 'rear' })
  expect(JSON.parse(String(patch.init.body))).not.toHaveProperty('sort_order')
  mounted.unmount(); render(tree())
  expect(await screen.findByLabelText('Маркер: Лидар')).toHaveStyle({ left: '40%', top: '30%' })
})

it.each(['matched', 'unknown', 'failure', 'unsupported'] as const)('shows the backend preview %s without exposing validation input', async state => {
  const normal = handler
  handler = (path, init) => path.endsWith('/preview') ? state === 'failure' ? json({ detail: [{ input: 'PRIVATE_VALUE' }] }, 422) : state === 'unsupported' ? json({ detail: 'unsupported_diagnostic_regex' }, 422) : json({ matched: state === 'matched', events: state === 'matched' ? [event] : [{ ...event, rule_id: null, part: null, view: null, x: null, y: null, indicator: null, title: 'Неизвестная ошибка' }] }) : normal(path, init)
  render(tree()); fireEvent.click(await screen.findByRole('button', { name: 'Проверить пример' }))
  const expected = { matched: /Совпадение найдено/, unknown: /Совпадение не найдено/, failure: /Проверьте поля правила/, unsupported: /не поддерживается/ }
  expect(await screen.findByText(expected[state])).toBeVisible()
  expect(screen.queryByText(/PRIVATE_VALUE/)).not.toBeInTheDocument()
})

it.each([409, 428])('reloads a stale catalog after reorder %s and retries with authoritative ETag and disabled IDs', async status => {
  let attempts = 0
  const normal = handler
  handler = (path, init) => {
    if (path.endsWith('/reorder') && ++attempts === 1) { etag = '"fresh"'; catalog = [first, second, { ...second, id: 3, title: 'Батарея' }]; return json({ detail: 'diagnostic_rules_changed' }, status) }
    return normal(path, init)
  }
  render(tree()); fireEvent.click(await screen.findByRole('button', { name: 'Ниже: Лидар' }))
  expect(await screen.findByText(/Каталог изменился.*повторите/i)).toBeVisible()
  expect(screen.getByRole('button', { name: 'Открыть правило Батарея' })).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Ниже: Лидар' }))
  await waitFor(() => expect(requests.filter(request => request.path.endsWith('/reorder'))).toHaveLength(2))
  const reorders = requests.filter(request => request.path.endsWith('/reorder'))
  expect(new Headers(reorders[0].init.headers).get('If-Match')).toBe('"v1"')
  expect(new Headers(reorders[1].init.headers).get('If-Match')).toBe('"fresh"')
  expect(JSON.parse(String(reorders[1].init.body))).toEqual({ ids: [2, 1, 3] })
})

it('disables separately from save and refreshes the catalog used for the next reorder', async () => {
  render(tree()); fireEvent.click(await screen.findByRole('button', { name: 'Отключить правило' }))
  await waitFor(() => expect(screen.getByLabelText('Правило включено')).not.toBeChecked())
  fireEvent.click(screen.getByRole('button', { name: 'Ниже: Лидар' }))
  await waitFor(() => expect(requests.some(request => request.path.endsWith('/reorder'))).toBe(true))
  expect(new Headers(requests.find(request => request.path.endsWith('/reorder'))!.init.headers).get('If-Match')).toBe('"disabled"')
  expect(requests.some(request => request.init.method === 'DELETE' || request.init.method === 'PATCH')).toBe(false)
})

it.each(['preview', 'save'] as const)('does not publish late %s into a newly selected rule', async operation => {
  let complete!: (response: Response) => void
  const normal = handler
  handler = (path, init) => (operation === 'preview' ? path.endsWith('/preview') : init.method === 'PATCH') ? new Promise(resolve => { complete = resolve }) : normal(path, init)
  render(tree()); fireEvent.click(await screen.findByRole('button', { name: operation === 'preview' ? 'Проверить пример' : 'Сохранить правило' }))
  fireEvent.click(screen.getByRole('button', { name: 'Открыть правило Колесо' }))
  await act(async () => complete(json(operation === 'preview' ? { matched: true, events: [event] } : { ...first, title: 'Поздний ответ' })))
  expect(screen.getByLabelText('Название ошибки')).toHaveValue('Колесо')
  expect(screen.queryByText(/Совпадение найдено|Правило сохранено/)).not.toBeInTheDocument()
  expect(screen.getByLabelText('Адрес')).toHaveTextContent('rule=2')
})

it('invalidates a pending preview when its view or example changes', async () => {
  let complete!: (response: Response) => void
  const normal = handler
  handler = (path, init) => path.endsWith('/preview') ? new Promise(resolve => { complete = resolve }) : normal(path, init)
  render(tree()); fireEvent.click(await screen.findByRole('button', { name: 'Проверить пример' }))
  fireEvent.change(screen.getByLabelText('Ракурс'), { target: { value: 'top' } })
  await act(async () => complete(json({ matched: true, events: [event] })))
  expect(screen.queryByText('Совпадение найдено')).not.toBeInTheDocument()
  expect(screen.getByLabelText('Ракурс')).toHaveValue('top')
})

it.each(['park', 'auth'] as const)('retires pending lists when the %s owner changes', async owner => {
  let complete!: (response: Response) => void; let calls = 0
  const normal = handler
  handler = (path, init) => path === '/api/admin/diagnostic-rules' && !init.method && ++calls === 1 ? new Promise(resolve => { complete = resolve }) : normal(path, init)
  const mounted = render(tree())
  if (owner === 'park') fireEvent.click(screen.getByText('Другой парк'))
  else mounted.rerender(tree({ ...user, id: 9 }))
  await screen.findByLabelText('Название ошибки')
  await act(async () => complete(json([{ ...first, title: 'Старый каталог' }], 200, { ETag: '"old"' })))
  expect(screen.getByLabelText('Название ошибки')).toHaveValue('Лидар')
  expect(screen.queryByText('Старый каталог')).not.toBeInTheDocument()
})

it('keeps the old Emergency fields available and preserves park while opening and backing out of rule detail', async () => {
  render(tree(user, '/admin/emergency/config?park=7'))
  expect(await screen.findByLabelText('Путь поля 9')).toHaveValue('data.status')
  fireEvent.click(screen.getByRole('tab', { name: 'Ошибки и индикация' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Открыть правило Лидар' }))
  fireEvent.click(screen.getByRole('button', { name: 'Назад к списку' }))
  expect(screen.getByLabelText('Адрес')).toHaveTextContent('park=7&tab=indication')
  expect(screen.getByLabelText('Адрес')).not.toHaveTextContent('rule=')
  fireEvent.click(screen.getByRole('tab', { name: 'Разделы и поля' }))
  expect(await screen.findByLabelText('Путь поля 9')).toHaveValue('data.status')
})

it('does not expose the rule editor to a custom role with settings navigation permission', async () => {
  render(tree({ ...user, role: 'field_lead' }))
  expect(screen.queryByRole('tab', { name: 'Ошибки и индикация' })).not.toBeInTheDocument()
  await screen.findByLabelText('Путь поля 9')
  expect(requests.some(request => request.path.startsWith('/api/admin/diagnostic-rules'))).toBe(false)
})

it('creates a complete rule, keeps an intentional disabled state and opens the persisted identity', async () => {
  render(tree(user, '/admin/emergency/config?park=7&tab=indication&rule=new'))
  await screen.findByLabelText('Название ошибки')
  expect(screen.getByRole('button', { name: 'Сохранить правило' })).toBeDisabled()
  for (const [label, value] of [['Название ошибки', 'Батарея'], ['Часть робота', 'Батарея'], ['Путь источника', 'data.errors'], ['Код или шаблон', 'BAT'], ['Расшифровка', 'Заменить батарею'], ['Пример входного значения', 'BAT']]) fireEvent.change(screen.getByLabelText(label), { target: { value } })
  fireEvent.click(screen.getByLabelText('Правило включено'))
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить правило' }))
  await waitFor(() => expect(screen.getByLabelText('Адрес')).toHaveTextContent('rule=3'))
  expect(screen.getByLabelText('Название ошибки')).toHaveValue('Батарея')
  const create = requests.find(request => request.path === '/api/admin/diagnostic-rules' && request.init.method === 'POST')!
  expect(JSON.parse(String(create.init.body))).toMatchObject({ is_enabled: false, sort_order: 2, source_path: 'data.errors', example: 'BAT', preferred_view: 'front' })
})

it('retains a successful creation if its follow-up list refresh fails, preventing accidental duplicate submission', async () => {
  let reads = 0
  const normal = handler
  handler = (path, init) => path === '/api/admin/diagnostic-rules' && !init.method && ++reads > 1 ? json({}, 503) : normal(path, init)
  render(tree(user, '/admin/emergency/config?park=7&tab=indication&rule=new'))
  await screen.findByLabelText('Название ошибки')
  for (const [label, value] of [['Название ошибки', 'Батарея'], ['Часть робота', 'Батарея'], ['Путь источника', 'errors'], ['Код или шаблон', 'BAT'], ['Расшифровка', 'Проверить батарею'], ['Пример входного значения', 'BAT']]) fireEvent.change(screen.getByLabelText(label), { target: { value } })
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить правило' }))
  await waitFor(() => expect(screen.getByLabelText('Адрес')).toHaveTextContent('rule=3'))
  expect(screen.getByLabelText('Название ошибки')).toHaveValue('Батарея')
  expect(screen.getByText('Не удалось обновить каталог')).toBeVisible()
  expect(requests.filter(request => request.path === '/api/admin/diagnostic-rules' && request.init.method === 'POST')).toHaveLength(1)
})

it.each([401, 403])('clears protected rule controls when preview returns %s', async status => {
  const normal = handler
  handler = (path, init) => path.endsWith('/preview') ? json({}, status) : normal(path, init)
  render(tree()); fireEvent.click(await screen.findByRole('button', { name: 'Проверить пример' }))
  expect(await screen.findByText('Каталог недоступен')).toBeVisible()
  expect(screen.queryByLabelText('Название ошибки')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Открыть правило Лидар' })).not.toBeInTheDocument()
})

it.each(['park', 'auth', 'view'] as const)('does not let an old save overwrite a changed %s owner or draft', async owner => {
  let complete!: (response: Response) => void
  const normal = handler
  handler = (path, init) => init.method === 'PATCH' ? new Promise(resolve => { complete = resolve }) : normal(path, init)
  const mounted = render(tree()); fireEvent.click(await screen.findByRole('button', { name: 'Сохранить правило' }))
  if (owner === 'park') fireEvent.click(screen.getByText('Другой парк'))
  else if (owner === 'auth') mounted.rerender(tree({ ...user, id: 9 }))
  else fireEvent.change(screen.getByLabelText('Ракурс'), { target: { value: 'rear' } })
  await screen.findByLabelText('Название ошибки')
  await act(async () => complete(json({ ...first, title: 'Устаревший ответ' })))
  expect(screen.getByLabelText('Название ошибки')).toHaveValue('Лидар')
  if (owner === 'view') expect(screen.getByLabelText('Ракурс')).toHaveValue('rear')
  expect(screen.queryByText('Правило сохранено.')).not.toBeInTheDocument()
})
