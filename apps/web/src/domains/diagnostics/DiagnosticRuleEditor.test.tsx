import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, type User } from '../../api'
import { StrictMode } from 'react'
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
function Probe() { const location = useLocation(); const navigate = useNavigate(); return <><output aria-label="Адрес">{location.search}</output><output aria-label="Ключ навигации">{location.key}</output><button onClick={() => navigate('?park=8&tab=indication&rule=1')}>Другой парк</button></> }
function tree(principal = user, entry = '/admin/emergency/config?park=7&tab=indication&rule=1') {
  return <MemoryRouter initialEntries={[entry]}><AuthContext.Provider value={{ user: principal, loading: false, login: async () => principal, refreshUser: async () => principal, logout: async () => undefined }}><AdminEmergencyConfig /><Probe /></AuthContext.Provider></MemoryRouter>
}
beforeEach(() => {
  resourceStore.clearAll(); requests.length = 0; catalog = [first, second]; etag = '"v1"'
  handler = (path, init) => {
    if (path === '/api/admin/emergency/sections') return json([{ id: 'state', title: 'Состояние', is_enabled: true, roles: ['admin'], sort_order: 0, fields: [{ id: 9, path: 'data.status', label: 'Статус', sort_order: 0 }] }])
    if (path.endsWith('/preview')) return json({ matched: true, events: [event] })
    if (path === '/api/admin/diagnostic-rules' && init.method === 'POST') { const created = { ...first, ...JSON.parse(String(init.body)), id: 3 }; catalog = [...catalog, created]; return json(created, 201) }
    if (init.method === 'PATCH') { const id = Number(path.split('/').at(-1)); catalog = catalog.map(rule => rule.id === id ? { ...rule, ...JSON.parse(String(init.body)) } : rule); return json(catalog.find(rule => rule.id === id)) }
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

it('offers a collapse control for the detailed diagnostic rule form', async () => {
  render(tree())

  expect(await screen.findByRole('button', { name: 'Свернуть: Редактирование правила' })).toBeVisible()
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

it.each([['1', 200], ['1', 422], ['new', 200], ['new', 422]] as const)('keeps selection %s a navigation no-op and settles a delayed preview %s', async (selected, status) => {
  let complete!: (response: Response) => void; let previews = 0
  const normal = handler
  handler = (path, init) => path.endsWith('/preview') && ++previews === 1 ? new Promise(resolve => { complete = resolve }) : normal(path, init)
  const query = `?park=7&tab=indication&rule=${selected}&filter=unresolved&sort=title`
  render(tree(user, `/admin/emergency/config${query}`))
  await screen.findByLabelText('Название ошибки')
  if (selected === 'new') fillNewRule()
  const key = screen.getByLabelText('Ключ навигации').textContent
  fireEvent.click(screen.getByRole('button', { name: 'Проверить пример' }))
  fireEvent.click(screen.getByRole('button', { name: selected === 'new' ? 'Новое правило' : 'Открыть правило Лидар' }))
  await act(async () => complete(status === 200 ? json({ matched: true, events: [event] }) : json({}, status)))
  expect(screen.getByRole('button', { name: 'Проверить пример' })).toBeEnabled()
  expect(screen.getByText(status === 200 ? 'Совпадение найдено' : /Проверьте поля правила и пример/)).toBeVisible()
  expect(screen.getByLabelText('Ключ навигации')).toHaveTextContent(key!)
  expect(screen.getByLabelText('Адрес').textContent).toBe(query)
  expect(screen.getByLabelText('Название ошибки')).toHaveValue(selected === 'new' ? 'Батарея' : 'Лидар')
  fireEvent.click(screen.getByRole('button', { name: 'Проверить пример' }))
  await screen.findByText('Совпадение найдено')
  expect(previews).toBe(2)
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
  fireEvent.click(await screen.findByRole('button', { name: 'Открыть раздел Состояние' }))
  expect(await screen.findByLabelText('Путь поля 9')).toHaveValue('data.status')
  fireEvent.click(screen.getByRole('tab', { name: 'Ошибки и индикация' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Открыть правило Лидар' }))
  fireEvent.click(screen.getByRole('button', { name: 'Назад к списку' }))
  expect(screen.getByLabelText('Адрес')).toHaveTextContent('park=7&tab=indication')
  expect(screen.getByLabelText('Адрес')).not.toHaveTextContent('rule=')
  fireEvent.click(screen.getByRole('tab', { name: 'Разделы и поля' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Открыть раздел Состояние' }))
  expect(await screen.findByLabelText('Путь поля 9')).toHaveValue('data.status')
})

it('does not expose the rule editor to a custom role with settings navigation permission', async () => {
  render(tree({ ...user, role: 'field_lead' }))
  expect(screen.queryByRole('tab', { name: 'Ошибки и индикация' })).not.toBeInTheDocument()
  fireEvent.click(await screen.findByRole('button', { name: 'Открыть раздел Состояние' }))
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

function fillNewRule() {
  for (const [label, value] of [['Название ошибки', 'Батарея'], ['Часть робота', 'Батарея'], ['Путь источника', 'errors'], ['Код или шаблон', 'BAT'], ['Расшифровка', 'Проверить батарею'], ['Пример входного значения', 'BAT']]) fireEvent.change(screen.getByLabelText(label), { target: { value } })
}

it.each(['post', 'refresh'] as const)('promotes a committed creation and preserves edits made during pending %s for the next PATCH', async pending => {
  let complete!: (response: Response) => void; let reads = 0
  const normal = handler
  handler = (path, init) => {
    if (pending === 'post' && path === '/api/admin/diagnostic-rules' && init.method === 'POST') return new Promise(resolve => { complete = resolve })
    if (pending === 'refresh' && path === '/api/admin/diagnostic-rules' && !init.method && ++reads === 2) return new Promise(resolve => { complete = resolve })
    return normal(path, init)
  }
  render(tree(user, '/admin/emergency/config?park=7&tab=indication&rule=new'))
  await screen.findByLabelText('Название ошибки'); fillNewRule()
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить правило' }))
  await waitFor(() => expect(complete).toBeTypeOf('function'))
  fireEvent.change(screen.getByLabelText('Название ошибки'), { target: { value: 'Батарея после отправки' } })
  fireEvent.change(screen.getByLabelText('Ракурс'), { target: { value: 'rear' } })
  const created = { ...first, title: 'Батарея', part: 'Батарея', id: 3 }
  if (pending === 'post') catalog = [...catalog, created]
  await act(async () => complete(json(pending === 'post' ? created : catalog, pending === 'post' ? 201 : 200, { ETag: '"created"' })))
  await waitFor(() => expect(screen.getByLabelText('Адрес')).toHaveTextContent('rule=3'))
  expect(screen.getByLabelText('Название ошибки')).toHaveValue('Батарея после отправки')
  expect(screen.getByLabelText('Ракурс')).toHaveValue('rear')
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить правило' }))
  await waitFor(() => expect(requests.some(request => request.path === '/api/admin/diagnostic-rules/3' && request.init.method === 'PATCH')).toBe(true))
  expect(JSON.parse(String(requests.find(request => request.path.endsWith('/3') && request.init.method === 'PATCH')!.init.body))).toMatchObject({ title: 'Батарея после отправки', preferred_view: 'rear' })
  expect(requests.filter(request => request.path === '/api/admin/diagnostic-rules' && request.init.method === 'POST')).toHaveLength(1)
  await screen.findByText('Правило сохранено.')
  expect(screen.getByLabelText('Название ошибки')).toHaveValue('Батарея после отправки')
  expect(catalog.find(rule => rule.id === 3)).toMatchObject({ title: 'Батарея после отправки', preferred_view: 'rear' })
})

it.each(['selection', 'park', 'auth'] as const)('does not promote an old creation into a changed %s owner', async owner => {
  let complete!: (response: Response) => void
  const normal = handler
  handler = (path, init) => path === '/api/admin/diagnostic-rules' && init.method === 'POST' ? new Promise(resolve => { complete = resolve }) : normal(path, init)
  const mounted = render(tree(user, '/admin/emergency/config?park=7&tab=indication&rule=new'))
  await screen.findByLabelText('Название ошибки'); fillNewRule()
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить правило' }))
  if (owner === 'selection') fireEvent.click(screen.getByRole('button', { name: 'Открыть правило Колесо' }))
  else if (owner === 'park') fireEvent.click(screen.getByText('Другой парк'))
  else mounted.rerender(tree({ ...user, id: 9 }, '/admin/emergency/config?park=7&tab=indication&rule=new'))
  const created = { ...first, id: 3, title: 'Старое создание' }; catalog = [...catalog, created]
  await act(async () => complete(json(created, 201)))
  expect(screen.getByLabelText('Адрес')).not.toHaveTextContent('rule=3')
  expect(screen.getByLabelText('Название ошибки')).toHaveValue(owner === 'selection' ? 'Колесо' : owner === 'park' ? 'Лидар' : '')
})

it.each([['X', '9'], ['X', '-1'], ['Y', '9'], ['Y', '-1']])('does not draw out-of-bounds %s=%s and explains why save is blocked', async (axis, value) => {
  render(tree())
  const coordinate = await screen.findByLabelText(`Координата ${axis}`)
  fireEvent.change(coordinate, { target: { value } })
  expect(screen.queryByLabelText('Маркер: Лидар')).not.toBeInTheDocument()
  expect(coordinate).toHaveValue(Number(value))
  expect(coordinate).toHaveAttribute('aria-invalid', 'true')
  expect(screen.getByText('Укажите число от 0 до 1.')).toBeVisible()
  expect(screen.getByRole('button', { name: 'Сохранить правило' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Проверить пример' })).toBeDisabled()
  fireEvent.change(coordinate, { target: { value: '.5' } })
  expect(screen.getByLabelText('Маркер: Лидар')).toHaveStyle(axis === 'X' ? { left: '50%' } : { top: '50%' })
  expect(coordinate).not.toHaveAttribute('aria-invalid', 'true')
  expect(screen.getByRole('button', { name: 'Сохранить правило' })).toBeEnabled()
})

it.each(['disable', 'save', 'reorder', 'reload'] as const)('invalidates a pending preview after authoritative %s and permits only a fresh preview', async operation => {
  let complete!: (response: Response) => void; let previewReads = 0; let listReads = 0
  const normal = handler
  handler = (path, init) => {
    if (path.endsWith('/preview')) {
      if (++previewReads === 1) return new Promise(resolve => { complete = resolve })
      const enabled = JSON.parse(String(init.body)).rule.is_enabled
      return json({ matched: enabled, events: enabled ? [event] : [] })
    }
    if (operation === 'reload' && path === '/api/admin/diagnostic-rules' && !init.method && ++listReads === 2) return json({}, 503)
    return normal(path, init)
  }
  render(tree()); await screen.findByLabelText('Название ошибки')
  if (operation === 'reload') {
    fireEvent.click(screen.getByRole('button', { name: 'Ниже: Лидар' }))
    await screen.findByText('Не удалось обновить каталог')
  }
  fireEvent.click(screen.getByRole('button', { name: 'Проверить пример' }))
  const previewRequest = requests.find(request => request.path.endsWith('/preview'))!
  fireEvent.click(screen.getByRole('button', { name: operation === 'disable' ? 'Отключить правило' : operation === 'save' ? 'Сохранить правило' : operation === 'reorder' ? 'Ниже: Лидар' : 'Повторить' }))
  if (operation === 'disable') await waitFor(() => expect(screen.getByLabelText('Правило включено')).not.toBeChecked())
  else if (operation === 'save') await screen.findByText('Правило сохранено.')
  else if (operation === 'reorder') await screen.findByText('Порядок сохранён.')
  else await waitFor(() => expect(screen.queryByText('Не удалось обновить каталог')).not.toBeInTheDocument())
  await act(async () => complete(json({ matched: true, events: [event] })))
  expect(screen.queryByText('Совпадение найдено')).not.toBeInTheDocument()
  expect(previewRequest.init.signal?.aborted).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: 'Проверить пример' }))
  expect(await screen.findByText(operation === 'disable' ? 'Совпадение не найдено' : 'Совпадение найдено')).toBeVisible()
  const latest = requests.filter(request => request.path.endsWith('/preview')).at(-1)!
  expect(JSON.parse(String(latest.init.body)).rule.is_enabled).toBe(operation !== 'disable')
})


it('automatically refreshes the diagnostic catalog without resetting the rule draft', async () => {
  render(tree())
  const title = await screen.findByLabelText('Название ошибки')
  fireEvent.change(title, { target: { value: 'Мой черновик' } })
  catalog = [{ ...first, title: 'Изменение другого администратора' }, second]
  etag = '"background-version"'
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 120_001)
  fireEvent.focus(window)
  await screen.findByRole('button', { name: 'Открыть правило Изменение другого администратора' })
  expect(title).toHaveValue('Мой черновик')
  expect(requests.filter(request => request.path === '/api/admin/diagnostic-rules' && !request.init.method)).toHaveLength(2)
})


it('loads the diagnostic catalog after StrictMode cancels its first mount request', async () => {
  const read = vi.spyOn(api, 'diagnosticRules')
    .mockImplementationOnce(signal => new Promise((_, reject) => {
      signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
    }))
    .mockResolvedValue({ rules: [first, second] as Awaited<ReturnType<typeof api.diagnosticRules>>['rules'], etag })
  render(<StrictMode>{tree()}</StrictMode>)
  expect(await screen.findByLabelText('Название ошибки')).toHaveValue(first.title)
  expect(read).toHaveBeenCalledTimes(2)
})

// Keep lifecycle assertions deterministic; pollingCapacity tests exercise jitter.
beforeEach(() => { vi.spyOn(Math, 'random').mockReturnValue(0) })

const sampleResult = { items: [
  { id: 9, outcome: 'matched', overlap_rule_ids: [2], reason: null },
  { id: 8, outcome: 'missed', overlap_rule_ids: [], reason: null },
  { id: 7, outcome: 'skipped', overlap_rule_ids: [], reason: 'legacy' },
], matched: 1, missed: 1, skipped: 1, overlapping: 1, limit: 50, has_more: true, budget_exhausted: false, invalid_rule_ids: [] }

it('tests collected originals on demand and keeps publication explicit', async () => {
  const normal = handler
  handler = (path, init) => path.endsWith('/test-samples') ? json(sampleResult) : normal(path, init)
  render(tree())
  const check = await screen.findByRole('button', { name: 'Проверить собранные ошибки' })
  expect(requests.some(request => request.path.endsWith('/test-samples'))).toBe(false)
  fireEvent.change(screen.getByLabelText('Код или шаблон'), { target: { value: 'DRAFT' } })
  fireEvent.click(check)
  expect(await screen.findByText('Совпало: 1 · Не совпало: 1 · Пропущено: 1')).toBeVisible()
  fireEvent.click(screen.getByText('Образцы и пересечения'))
  expect(screen.getByText('Образец #9')).toBeVisible()
  expect(screen.getByText('Также распознают правила: #2')).toBeVisible()
  expect(screen.getByText(/Показаны последние 50 образцов/)).toBeVisible()
  expect(screen.getByText(/Нет сохранённого оригинала/)).toBeVisible()
  const sent = requests.find(request => request.path.endsWith('/test-samples'))!
  expect(JSON.parse(String(sent.init.body))).toMatchObject({ rule: { pattern: 'DRAFT' }, exclude_rule_id: 1, limit: 50 })
  expect(requests.filter(request => request.init.method === 'POST')).toHaveLength(1)
  expect(screen.getByRole('button', { name: 'Сохранить правило' })).toBeEnabled()
  fireEvent.change(screen.getByLabelText('Код или шаблон'), { target: { value: 'ANOTHER' } })
  expect(screen.queryByText('Образец #9')).not.toBeInTheDocument()
})

it.each(['draft', 'selection', 'park', 'auth', 'catalog'] as const)('retires delayed sample evaluation when %s changes', async owner => {
  let complete!: (response: Response) => void
  const normal = handler
  handler = (path, init) => path.endsWith('/test-samples') ? new Promise(resolve => { complete = resolve }) : normal(path, init)
  const mounted = render(tree())
  fireEvent.click(await screen.findByRole('button', { name: 'Проверить собранные ошибки' }))
  if (owner === 'draft') fireEvent.change(screen.getByLabelText('Название ошибки'), { target: { value: 'Черновик' } })
  else if (owner === 'selection') fireEvent.click(screen.getByRole('button', { name: 'Открыть правило Колесо' }))
  else if (owner === 'park') fireEvent.click(screen.getByText('Другой парк'))
  else if (owner === 'auth') mounted.rerender(tree({ ...user, id: 9 }))
  else {
    fireEvent.click(screen.getByRole('button', { name: 'Ниже: Лидар' }))
    await screen.findByText('Порядок сохранён.')
  }
  await act(async () => complete(json(sampleResult)))
  expect(screen.queryByText('Образец #9')).not.toBeInTheDocument()
  expect(requests.find(request => request.path.endsWith('/test-samples'))!.init.signal?.aborted).toBe(true)
})

it.each([401, 403])('clears sample results and protected controls on denied evaluation %s', async status => {
  const normal = handler
  handler = (path, init) => path.endsWith('/test-samples') ? json({}, status) : normal(path, init)
  render(tree())
  fireEvent.click(await screen.findByRole('button', { name: 'Проверить собранные ошибки' }))
  expect(await screen.findByText('Каталог недоступен')).toBeVisible()
  expect(screen.queryByRole('button', { name: 'Проверить собранные ошибки' })).not.toBeInTheDocument()
})

it('explains incomplete and empty evaluations without treating skipped samples as misses', async () => {
  const normal = handler
  let calls = 0
  handler = (path, init) => path.endsWith('/test-samples') ? json(++calls === 1
    ? { ...sampleResult, budget_exhausted: true, invalid_rule_ids: [77], items: [{ id: 7, outcome: 'skipped', overlap_rule_ids: [], reason: 'budget' }], matched: 0, missed: 0, skipped: 1, overlapping: 0 }
    : { ...sampleResult, items: [], matched: 0, missed: 0, skipped: 0, overlapping: 0, has_more: false }) : normal(path, init)
  render(tree())
  fireEvent.click(await screen.findByRole('button', { name: 'Проверить собранные ошибки' }))
  expect(await screen.findByText(/Проверка завершена частично/)).toBeVisible()
  expect(screen.getByText('Совпало: 0 · Не совпало: 0 · Пропущено: 1')).toBeVisible()
  expect(screen.getByText('Не удалось проверить пересечения с правилами: #77.')).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Проверить собранные ошибки' }))
  expect(await screen.findByText('Собранных образцов пока нет.')).toBeVisible()
  expect(screen.queryByText(/Проверка завершена частично/)).not.toBeInTheDocument()
})
