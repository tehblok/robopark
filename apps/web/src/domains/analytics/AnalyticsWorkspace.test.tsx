import { StrictMode } from 'react'
import { beforeEach } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, type User, type Park } from '../../api'
import { AuthContext } from '../../auth-context'
import { ParkScopeContext } from '../../app/park/parkScope'
import { resourceStore } from '../../lib/resource'
import { Analytics } from '../../pages/Analytics'
import { deferred, makeUser, otherPark, park } from '../insights/operations.test-support'
import { analyticsFixture as fixture } from './analytics.test-support'
type Client = {
  analytics: (parkId: number, days: number, bucket: '2h' | '1d') => Promise<ReturnType<typeof fixture>>
  analyticsTaskKeys?: (parkId: number, days: number, bucket: '2h' | '1d', periodEnd: string, group: string, key: string | undefined, offset: number, after?: string) => Promise<{ task_keys: string[]; total: number; has_more: boolean }>
}
function Location() { return <output aria-label="URL">{useLocation().search}</output> }
function tree({ client, url = '/analytics?park=7', user = makeUser(), selectedPark = park, parks = [park, otherPark], allowAllParks = false, refreshUser = vi.fn(async () => user) }: { client: Client; url?: string; user?: User; selectedPark?: Park | null; parks?: Park[]; allowAllParks?: boolean; refreshUser?: () => Promise<User> }) {
  return <MemoryRouter initialEntries={[url]}><AuthContext.Provider value={{ user, loading: false, login: async () => user, refreshUser, logout: async () => {} }}><ParkScopeContext.Provider value={{ selectedPark, parkId: selectedPark?.id ?? null, parks, allowAllParks, loading: false, locked: false, setParkId: vi.fn(), refreshParks: async () => {} }}><Analytics apiClient={client} /><Location /></ParkScopeContext.Provider></AuthContext.Provider></MemoryRouter>
}
afterEach(() => { resourceStore.clearAll(); vi.restoreAllMocks(); vi.useRealTimers() })

it('shows one actionable empty state instead of pages of unmeasured charts on a fresh installation', async () => {
  const base = fixture()
  const emptyMetric = <T extends { value: number | null; sample_count: number; observed_buckets: number; task_keys: string[] }>(metric: T): T => ({
    ...metric, value: null, sample_count: 0, observed_buckets: 0, task_keys: [],
  })
  const empty = {
    ...base,
    coverage: Object.fromEntries(Object.entries(base.coverage).map(([key, value]) => [key, { ...value, observed_buckets: 0, complete: false }])),
    series: Object.fromEntries(Object.entries(base.series).map(([key, value]) => [key, emptyMetric(value)])),
    backlog_age_bands: base.backlog_age_bands.map(emptyMetric),
    sla_trend: emptyMetric(base.sla_trend),
    stage_durations: base.stage_durations.map(emptyMetric),
    workload: base.workload.map(emptyMetric),
    verified_closures: { ...base.verified_closures, count: 0, task_keys: [] },
    drilldown_task_keys: [],
  }
  const user = makeUser({ role: 'royal', permissions: ['nav.analytics', 'tracker.read', 'nav.admin'] })
  render(tree({ client: { analytics: vi.fn(async () => empty) }, user }))

  expect(await screen.findByRole('heading', { name: 'История пока не собрана' })).toBeVisible()
  expect(screen.getByText(/Покрытие: поток 0\/12, снимки 0\/12/)).toBeVisible()
  expect(screen.getByRole('link', { name: 'Проверить настройки Tracker' })).toHaveAttribute('href', '/admin/settings?park=7&tab=integrations#tracker-token')
  expect(screen.queryByRole('heading', { name: 'Динамика процесса' })).not.toBeInTheDocument()
  expect(screen.queryByRole('heading', { name: 'Срез периода · Север' })).not.toBeInTheDocument()
})

it('loads historical analytics with trends, coverage and genuine task drilldowns', async () => {
  const client = { analytics: vi.fn(async () => fixture()) }
  render(tree({ client }))
  expect(await screen.findByRole('heading', { name: 'Динамика процесса' })).toBeVisible()
  for (const heading of ['Простой незавершённых задач', 'Динамика SLA']) {
    expect(screen.getByRole('heading', { name: heading })).toBeVisible()
  }
  expect(screen.getByText(/часовому поясу первого входа задачи в очередь/)).toBeVisible()
  fireEvent.click(screen.getByText('Этапы работы и связанные задачи'))
  for (const heading of ['Наблюдаемая длительность этапов', 'Нагрузка по этапам']) {
    expect(screen.getByRole('heading', { name: heading })).toBeVisible()
  }
  expect(screen.queryByRole('heading', { name: 'Текущие задачи' })).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Статус задач')).not.toBeInTheDocument()
  expect(screen.getByText(/Неполная история/)).toBeVisible()
  const sla = screen.getByRole('heading', { name: 'Динамика SLA' })
  const age = screen.getByRole('heading', { name: 'Простой незавершённых задач' })
  expect(sla.compareDocumentPosition(age) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(age.closest('details')).not.toHaveAttribute('open')
  fireEvent.click(age)
  expect(screen.getByText(/Простой считается непрерывно от первого входа в очередь/)).toBeVisible()
  expect(screen.getAllByText('Нет наблюдений').length).toBeGreaterThan(0)
  const drilldown = screen.getByText('Задачи в наблюдениях (1)')
  fireEvent.click(drilldown)
  expect(within(drilldown.closest('details')!).getByRole('link', { name: 'Открыть задачу ROBOPARK-42' })).toHaveAttribute('href', '/work/ROBOPARK-42?park=7')
  expect(client.analytics).toHaveBeenCalledWith(7, 7, '1d')
})

it('gives an operator the coverage-aware operational assessment first', async () => {
  render(tree({ client: { analytics: vi.fn(async () => fixture()) } }))

  expect(await screen.findByRole('heading', { name: 'Анализ текущей ситуации' })).toBeVisible()
  expect(screen.getByText(/Данных недостаточно для вывода об отклонениях/)).toBeVisible()
})

it('does not draw a trend from one measured interval', async () => {
  render(tree({ client: { analytics: vi.fn(async () => fixture()) } }))

  const card = (await screen.findByRole('heading', { name: 'Поступило за период' })).closest('article')!
  expect(within(card).getByText(/Для графика нужны хотя бы два соседних наблюдения/)).toBeVisible()
  expect(within(card).queryByRole('img', { name: /Поступило за период: динамика/ })).not.toBeInTheDocument()
  expect(within(card).getByText('Значения по интервалам')).toBeVisible()
})

it('keeps closure numbers visible while deferring methodology and stage detail', async () => {
  render(tree({ client: { analytics: vi.fn(async () => fixture()) } }))

  const closures = await screen.findByRole('region', { name: 'Подтверждённые закрытия' })
  expect(within(closures).getByText('Не менее 1 закрытой задачи')).toBeVisible()
  const method = within(closures).getByText('Как считаем закрытия и простой').closest('details')!
  expect(method).not.toHaveAttribute('open')
  fireEvent.click(within(method).getByText('Как считаем закрытия и простой'))
  expect(method).toHaveAttribute('open')
  expect(within(method).getByText(/Повторное закрытие одной задачи считается один раз/)).toBeVisible()

  const stages = screen.getByText('Этапы работы и связанные задачи').closest('details')!
  expect(stages).not.toHaveAttribute('open')
  fireEvent.click(screen.getByText('Этапы работы и связанные задачи'))
  expect(stages).toHaveAttribute('open')
  expect(within(stages).getByRole('heading', { name: 'Нагрузка по этапам' })).toBeVisible()
})

it('loads the next drilldown page only when requested', async () => {
  const analyticsTaskKeys = vi.fn(async () => ({ task_keys: ['ROBOPARK-43'], total: 2, has_more: false }))
  const client = { analytics: vi.fn(async () => ({
    ...fixture(), drilldown_task_keys_count: 2,
  })), analyticsTaskKeys }
  render(tree({ client }))
  fireEvent.click(await screen.findByText('Этапы работы и связанные задачи'))
  const summary = await screen.findByText('Задачи в наблюдениях (2)')
  expect(analyticsTaskKeys).not.toHaveBeenCalled()
  fireEvent.click(summary)
  fireEvent.click(within(summary.closest('details')!).getByRole('button', { name: 'Показать ещё задачи' }))
  expect(await within(summary.closest('details')!).findByRole('link', { name: 'Открыть задачу ROBOPARK-43' })).toBeVisible()
  expect(analyticsTaskKeys).toHaveBeenCalledWith(7, 7, '1d', fixture().period.end, 'all', undefined, 1, 'ROBOPARK-42')
})

it('removes cached analytics when a drilldown page loses authorization', async () => {
  const refreshUser = vi.fn(async () => makeUser())
  const client = {
    analytics: vi.fn(async () => ({ ...fixture(), drilldown_task_keys_count: 2 })),
    analyticsTaskKeys: vi.fn().mockRejectedValue(new ApiError(403)),
  }
  render(tree({ client, refreshUser }))
  fireEvent.click(await screen.findByText('Этапы работы и связанные задачи'))
  const summary = await screen.findByText('Задачи в наблюдениях (2)')
  fireEvent.click(summary)
  fireEvent.click(within(summary.closest('details')!).getByRole('button', { name: 'Показать ещё задачи' }))
  expect(await screen.findByRole('heading', { name: 'Нет доступа' })).toBeVisible()
  expect(screen.queryByRole('link', { name: 'Открыть задачу ROBOPARK-42' })).not.toBeInTheDocument()
  expect(refreshUser).toHaveBeenCalledTimes(1)
})

it('labels distinct verified closures as a lower bound and links the counted task', async () => {
  const client = { analytics: vi.fn(async () => fixture()) }
  render(tree({ client }))
  const section = await screen.findByRole('region', { name: 'Подтверждённые закрытия' })
  expect(section).toHaveTextContent('Не менее 1 закрытой задачи')
  expect(section).toHaveTextContent('Повторное закрытие одной задачи считается один раз')
  expect(section).toHaveTextContent('снова открытая задача возвращается в активный хвост')
  expect(section).toHaveTextContent('В пределах SLA: 100%')
  expect(section).toHaveTextContent('1 из 1 подтверждённых задач')
  fireEvent.click(within(section).getByText('Связанные задачи (1)'))
  expect(within(section).getByRole('link', { name: 'Открыть задачу ROBOPARK-42' })).toHaveAttribute('href', '/work/ROBOPARK-42?park=7')
})

it('shows measured calendar downtime for confirmed closures with its sample size', async () => {
  const client = { analytics: vi.fn(async () => ({
    ...fixture(), verified_closures: {
      ...fixture().verified_closures,
      count: 2, downtime_sample_count: 2, median_downtime_hours: 17, p90_downtime_hours: 28,
    },
  })) }
  render(tree({ client }))
  const section = await screen.findByRole('region', { name: 'Подтверждённые закрытия' })
  expect(within(section).getByText('Медиана простоя')).not.toBeVisible()
  fireEvent.click(within(section).getByText('Как считаем закрытия и простой'))
  expect(within(section).getByText('Медиана простоя')).toBeVisible()
  expect(section).toHaveTextContent('Медиана простоя')
  expect(section).toHaveTextContent('17.0 ч')
  expect(section).toHaveTextContent('90-й процентиль')
  expect(section).toHaveTextContent('28.0 ч')
  expect(section).toHaveTextContent('Измеренных закрытий: 2')
  expect(section).toHaveTextContent('включая время между закрытием и повторным открытием')
})

it('does not treat zero known closures as proof that no tasks were closed', async () => {
  const client = { analytics: vi.fn(async () => ({
    ...fixture(), verified_closures: { ...fixture().verified_closures, count: 0, task_keys: [] },
  })) }
  render(tree({ client }))
  const section = await screen.findByRole('region', { name: 'Подтверждённые закрытия' })
  expect(section).toHaveTextContent('В доступной истории закрытия не подтверждены')
  expect(section).not.toHaveTextContent('Закрыто 0 задач')
})

it('withholds closure evidence from a restricted status scope', async () => {
  const client = { analytics: vi.fn(async () => ({
    ...fixture(), verified_closures: { ...fixture().verified_closures, count: null, task_keys: [] },
  })) }
  render(tree({ client, user: makeUser({ role: 'driver' }) }))
  const section = await screen.findByRole('region', { name: 'Подтверждённые закрытия' })
  expect(section).toHaveTextContent('Недоступно для этой роли')
  expect(within(section).queryByRole('link', { name: /Открыть задачу/ })).not.toBeInTheDocument()
})

it('compares only verified closure lower bounds for accessible parks', async () => {
  const client = { analytics: vi.fn(async (id: number) => ({
    ...fixture(id), verified_closures: { ...fixture(id).verified_closures, count: id === 7 ? 1 : 2 },
  })) }
  render(tree({ client, url: '/analytics?park=7&compare=8' }))
  const table = await screen.findByRole('table', { name: 'Сравнение парков' })
  const row = within(table).getByRole('row', { name: /Подтверждённые закрытия/ })
  expect(within(row).getByText('≥ 1')).toBeVisible()
  expect(within(row).getByText('≥ 2')).toBeVisible()
})

it('shows each park history in its own timezone', async () => {
  const client = { analytics: vi.fn(async (id: number) => ({
    ...fixture(id),
    timezone: id === 7 ? 'Europe/Moscow' : 'Asia/Yekaterinburg',
  })) }
  render(tree({ client, url: '/analytics?park=7&compare=8' }))
  const north = await screen.findByRole('region', { name: 'История парка Север' })
  const south = await screen.findByRole('region', { name: 'История парка Юг' })
  expect(within(north).getByText(/06\.09, 15:00 · Europe\/Moscow/)).toBeVisible()
  expect(within(south).getByText(/06\.09, 17:00 · Asia\/Yekaterinburg/)).toBeVisible()
  expect(within(south).queryByText(/МСК/)).not.toBeInTheDocument()
})

it('explains Tracker history failures instead of showing them as empty SLA data', async () => {
  const client = { analytics: vi.fn(async () => ({
    ...fixture(), warnings: [...fixture().warnings, 'history_access_denied', 'history_source_unavailable'],
  })) }
  render(tree({ client }))
  expect(await screen.findByText(/Tracker отказал в доступе к истории статусов/)).toBeVisible()
  expect(screen.getByText(/История статусов временно недоступна в Tracker/)).toBeVisible()
})

it('explains invalid historical timezone instead of blaming missing queue history', async () => {
  const client = { analytics: vi.fn(async () => ({
    ...fixture(), warnings: [...fixture().warnings, 'invalid_history_timezone'],
  })) }
  render(tree({ client }))
  expect(await screen.findByText(/Некорректный часовой пояс в истории задачи/)).toBeVisible()
  expect(screen.queryByText(/не подтверждено время входа в очередь/)).not.toBeInTheDocument()
})

it('shows closed-history search and page-limit warnings beside closure metrics', async () => {
  const client = { analytics: vi.fn(async () => ({
    ...fixture(), warnings: [...fixture().warnings, 'closed_history_search_failed', 'closed_history_page_cap'],
  })) }
  render(tree({ client }))
  const history = await screen.findByRole('region', { name: 'История парка Север' })
  expect(within(history).getByText(/Страницу поиска закрытых задач в Tracker не удалось загрузить или обработать/)).toBeVisible()
  expect(within(history).getByText(/Достигнут предел страниц поиска Tracker/)).toBeVisible()
})

it('attributes a transferred task closure to the park at its closing transition', async () => {
  render(tree({ client: { analytics: vi.fn(async () => fixture()) } }))
  fireEvent.click(await screen.findByText('Как считаем закрытия и простой'))
  expect(await screen.findByText(/Парк определяется по подтверждённому переходу в закрытый статус/)).toBeVisible()
  expect(screen.queryByText(/Парк определяется по первому подтверждённому входу в очередь/)).not.toBeInTheDocument()
})

it('does not report a healthy process when the owner has no historical observations', async () => {
  const client = { analytics: vi.fn(async () => {
    const data = fixture()
    return { ...data, coverage: {
      flow: { ...data.coverage.flow, observed_buckets: 0 },
      observations: { ...data.coverage.observations, observed_buckets: 0 },
    } }
  }) }
  render(tree({ client, url: '/analytics?park=all', user: makeUser({ role: 'royal' }), selectedPark: null, parks: [park], allowAllParks: true }))
  expect(await screen.findByRole('heading', { name: 'Анализ текущей ситуации' })).toBeVisible()
  expect(screen.getByText(/Недостаточно наблюдений для оценки состояния/)).toBeVisible()
  expect(screen.queryByText(/Критичных отклонений по доступным данным не обнаружено/)).not.toBeInTheDocument()
})

it('explains sparse history instead of recommending a stage from a single sample', async () => {
  render(tree({ client: { analytics: vi.fn(async () => fixture(7, 7, '1d')) }, url: '/analytics?park=all', user: makeUser({ role: 'royal' }), selectedPark: null, parks: [park], allowAllParks: true }))
  expect(await screen.findByRole('heading', { name: 'Анализ текущей ситуации' })).toBeVisible()
  expect(screen.getByText(/Данных недостаточно для вывода об отклонениях/)).toBeVisible()
  expect(screen.queryByText(/Проверьте общую причину задержки/)).not.toBeInTheDocument()
})

it('does not declare the process healthy from a partial history with unknown measures', async () => {
  const client = { analytics: vi.fn(async () => {
    const data = fixture()
    return { ...data,
      backlog_age_bands: [], workload: [],
      series: {
        ...data.series,
        arrived: { ...data.series.arrived, value: null },
        departed: { ...data.series.departed, value: null },
        backlog: { ...data.series.backlog, value: null, points: [] },
      },
    }
  }) }
  render(tree({ client, url: '/analytics?park=all', user: makeUser({ role: 'royal' }), selectedPark: null, parks: [park], allowAllParks: true }))
  expect(await screen.findByRole('heading', { name: 'Анализ текущей ситуации' })).toBeVisible()
  expect(screen.getByText(/Данных недостаточно для вывода об отклонениях/)).toBeVisible()
  expect(screen.queryByText(/Критичных отклонений по доступным данным не обнаружено/)).not.toBeInTheDocument()
})

it('allows a quiet summary only with complete measured coverage', async () => {
  const client = { analytics: vi.fn(async () => {
    const data = fixture()
    return { ...data,
      coverage: { flow: { ...data.coverage.flow, complete: true }, observations: { ...data.coverage.observations, complete: true } },
      series: Object.fromEntries(Object.entries(data.series).map(([key, value]) => [key, { ...value, value: 0, points: [] }])),
      sla_trend: { ...data.sla_trend, value: 0 },
      backlog_age_bands: data.backlog_age_bands.map(band => ({ ...band, value: 0 })),
      workload: [],
    }
  }) }
  render(tree({ client, url: '/analytics?park=all', user: makeUser({ role: 'royal' }), selectedPark: null, parks: [park], allowAllParks: true }))
  expect(await screen.findByText(/Критичных отклонений по доступным данным не обнаружено/)).toBeVisible()
})

it('keeps historical filters independent of overview and compares only accessible parks', async () => {
  const client = { analytics: vi.fn(async (id: number) => fixture(id)) }
  render(tree({ client, url: '/analytics?park=7&days=30&status=moving' }))
  await screen.findByRole('heading', { name: 'Динамика процесса' })
  expect(client.analytics).toHaveBeenCalledWith(7, 7, '1d')
  fireEvent.change(screen.getByLabelText('Период аналитики'), { target: { value: '30' } })
  await waitFor(() => expect(client.analytics).toHaveBeenLastCalledWith(7, 30, '1d'))
  fireEvent.change(screen.getByLabelText('Шаг графиков'), { target: { value: '2h' } })
  await waitFor(() => expect(client.analytics).toHaveBeenLastCalledWith(7, 30, '2h'))
  fireEvent.change(screen.getByLabelText('Сравнить с парком'), { target: { value: '8' } })
  await waitFor(() => expect(client.analytics).toHaveBeenCalledWith(8, 30, '2h'))
  expect(await screen.findByRole('region', { name: 'История парка Юг' })).toBeVisible()
  expect(screen.getByRole('table', { name: 'Сравнение парков' })).toBeVisible()
  expect(screen.getByLabelText('URL')).toHaveTextContent('park=7&period=30&bucket=2h&compare=8')
  expect(screen.getByLabelText('URL')).not.toHaveTextContent('status=')
})

it('retains every filter when several controls change before navigation renders', async () => {
  const client = { analytics: vi.fn(async (id: number) => fixture(id)) }
  render(tree({ client }))
  await screen.findByRole('heading', { name: 'Динамика процесса' })
  act(() => {
    fireEvent.change(screen.getByLabelText('Период аналитики'), { target: { value: '1' } })
    fireEvent.change(screen.getByLabelText('Шаг графиков'), { target: { value: '2h' } })
    fireEvent.change(screen.getByLabelText('Сравнить с парком'), { target: { value: '8' } })
  })
  await waitFor(() => expect(screen.getByLabelText('URL')).toHaveTextContent('park=7&period=1&bucket=2h&compare=8'))
  expect(screen.getByLabelText('Период аналитики')).toHaveValue('1')
  expect(screen.getByLabelText('Шаг графиков')).toHaveValue('2h')
  expect(client.analytics).toHaveBeenCalledWith(8, 1, '2h')
})

it('keeps the analytics park and period choices in one compact control row', async () => {
  const client = { analytics: vi.fn(async () => fixture()) }
  render(tree({ client }))
  await screen.findByRole('heading', { name: 'Динамика процесса' })
  expect(screen.getByRole('group', { name: 'Параметры аналитики' })).toHaveClass('rp-analytics-controls--single-row')
})

it('normalizes an unauthorized comparison without issuing its request', async () => {
  const client = { analytics: vi.fn(async () => fixture()) }
  render(tree({ client, user: makeUser({ parks: [park] }), url: '/analytics?park=7&compare=8&period=999&bucket=5m' }))
  await screen.findByRole('heading', { name: 'Динамика процесса' })
  expect(client.analytics).toHaveBeenCalledTimes(1)
  expect(client.analytics).toHaveBeenCalledWith(7, 7, '1d')
  expect(within(screen.getByLabelText('Сравнить с парком')).queryByText('Юг')).not.toBeInTheDocument()
  expect(screen.getByLabelText('URL')).toHaveTextContent('?park=7')
})

it.each(['nav.analytics', 'tracker.read'])('denies analytics without %s', permission => {
  const client = { analytics: vi.fn(async () => fixture()) }
  render(tree({ client, user: makeUser({ permissions: ['nav.analytics', 'tracker.read'].filter(item => item !== permission) }) }))
  expect(screen.getByRole('heading', { name: 'Нет доступа' })).toBeVisible()
  expect(client.analytics).not.toHaveBeenCalled()
})

it('drops another park’s in-flight result and refreshes authorization once on 403', async () => {
  const pending = deferred<ReturnType<typeof fixture>>()
  const user = makeUser()
  const refreshUser = vi.fn(async () => ({ ...user }))
  const client = { analytics: vi.fn().mockImplementationOnce(() => pending.promise).mockRejectedValue(new ApiError(403)) }
  const view = render(tree({ client, user, refreshUser }))
  view.rerender(tree({ client, user, selectedPark: otherPark, refreshUser }))
  expect(await screen.findByRole('heading', { name: 'Нет доступа' })).toBeVisible()
  await act(async () => pending.resolve(fixture()))
  expect(screen.queryByRole('heading', { name: 'Динамика процесса' })).not.toBeInTheDocument()
  expect(refreshUser).toHaveBeenCalledTimes(1)
  expect(client.analytics).toHaveBeenCalledTimes(2)
})

it('recovers after disabling a comparison whose deferred request returned 403', async () => {
  const denied = deferred<ReturnType<typeof fixture>>()
  const user = makeUser()
  const refreshUser = vi.fn(async () => user)
  const client = { analytics: vi.fn((id: number) => id === 8 ? denied.promise : Promise.resolve(fixture(id))) }
  render(tree({ client, user, refreshUser, url: '/analytics?park=7&compare=8' }))
  await act(async () => denied.reject(new ApiError(403)))
  expect(await screen.findByRole('heading', { name: 'Нет доступа' })).toBeVisible()
  fireEvent.change(screen.getByLabelText('Сравнить с парком'), { target: { value: '' } })
  expect(await screen.findByRole('region', { name: 'История парка Север' })).toBeVisible()
  expect(screen.queryByRole('heading', { name: 'Нет доступа' })).not.toBeInTheDocument()
  expect(client.analytics.mock.calls.map(call => call[0])).toEqual([7, 8, 7])
  expect(refreshUser).toHaveBeenCalledTimes(1)
})

it('recovers on an accessible primary park after another park returned 403', async () => {
  const user = makeUser()
  const refreshUser = vi.fn(async () => user)
  const client = { analytics: vi.fn(async (id: number) => { if (id === 7) throw new ApiError(403); return fixture(id) }) }
  const view = render(tree({ client, user, refreshUser }))
  await screen.findByRole('heading', { name: 'Нет доступа' })
  view.rerender(tree({ client, user, refreshUser, selectedPark: otherPark }))
  expect(await screen.findByRole('region', { name: 'История парка Юг' })).toBeVisible()
  expect(client.analytics.mock.calls.map(call => call[0])).toEqual([7, 8])
})

it('does not retry unchanged denied access but recovers after refreshed access changes', async () => {
  const user = makeUser()
  const refreshUser = vi.fn(async () => ({ ...user }))
  const client = { analytics: vi.fn().mockRejectedValueOnce(new ApiError(403)).mockResolvedValue(fixture()) }
  const view = render(tree({ client, user, refreshUser }))
  await screen.findByRole('heading', { name: 'Нет доступа' })
  view.rerender(tree({ client, refreshUser, user: { ...user, permissions: [...user.permissions!].reverse(), parks: [...user.parks].reverse() } }))
  await act(async () => {})
  expect(screen.getByRole('heading', { name: 'Нет доступа' })).toBeVisible()
  expect(client.analytics).toHaveBeenCalledTimes(1)
  view.rerender(tree({ client, refreshUser, user: { ...user, permissions: [...user.permissions!, 'reports.create'] } }))
  expect(await screen.findByRole('region', { name: 'История парка Север' })).toBeVisible()
  expect(client.analytics).toHaveBeenCalledTimes(2)
  expect(refreshUser).toHaveBeenCalledTimes(1)
})

it('recovers after a denied filter context is changed', async () => {
  const client = { analytics: vi.fn(async (id: number, days: number) => { if (days === 7) throw new ApiError(403); return fixture(id, days) }) }
  render(tree({ client }))
  await screen.findByRole('heading', { name: 'Нет доступа' })
  fireEvent.change(screen.getByLabelText('Период аналитики'), { target: { value: '1' } })
  expect(await screen.findByRole('region', { name: 'История парка Север' })).toBeVisible()
  expect(client.analytics).toHaveBeenCalledTimes(2)
})

it('ignores a released comparison denial after the active context already recovered', async () => {
  const denied = deferred<ReturnType<typeof fixture>>()
  const user = makeUser()
  const refreshUser = vi.fn(async () => user)
  const client = { analytics: vi.fn((id: number) => id === 8 ? denied.promise : Promise.resolve(fixture(id))) }
  render(tree({ client, user, refreshUser, url: '/analytics?park=7&compare=8' }))
  fireEvent.change(screen.getByLabelText('Сравнить с парком'), { target: { value: '' } })
  await screen.findByRole('region', { name: 'История парка Север' })
  await act(async () => denied.reject(new ApiError(403)))
  expect(screen.getByRole('region', { name: 'История парка Север' })).toBeVisible()
  expect(screen.queryByRole('heading', { name: 'Нет доступа' })).not.toBeInTheDocument()
  expect(refreshUser).not.toHaveBeenCalled()
})


it('reuses fresh history on remount and refreshes stale history on focus', async () => {
  const client = { analytics: vi.fn(async () => fixture()) }
  const view = render(tree({ client }))
  await screen.findByRole('region', { name: 'История парка Север' })
  expect(screen.getByRole('button', { name: 'Обновить аналитику' })).toBeVisible()
  view.unmount()
  render(tree({ client }))
  expect(screen.getByRole('region', { name: 'История парка Север' })).toBeVisible()
  expect(client.analytics).toHaveBeenCalledTimes(1)
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 120_001)
  fireEvent.focus(window)
  await waitFor(() => expect(client.analytics).toHaveBeenCalledTimes(2))
  expect(Object.keys(localStorage).filter(key => key.includes('analytics:'))).toEqual([])
})

it.each([401, 403])('clears cached history after automatic %s revalidation and halts retries', async status => {
  const user = makeUser()
  const refreshUser = vi.fn(async () => user)
  const denied = deferred<ReturnType<typeof fixture>>()
  const client = { analytics: vi.fn().mockResolvedValueOnce(fixture()).mockImplementationOnce(() => denied.promise).mockRejectedValue(new ApiError(status)) }
  render(tree({ client, user, refreshUser }))
  await screen.findByRole('region', { name: 'История парка Север' })
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 120_001)
  fireEvent.focus(window)
  await waitFor(() => expect(client.analytics).toHaveBeenCalledTimes(2))
  await act(async () => {
    denied.reject(new ApiError(status))
    await denied.promise.catch(() => undefined)
    fireEvent.focus(window)
    fireEvent(window, new Event('online'))
  })
  await screen.findByRole(
    'heading',
    { name: status === 401 ? 'Сессия истекла' : 'Нет доступа' },
    { timeout: 3_000 },
  )
  expect(screen.queryByRole('region', { name: 'История парка Север' })).not.toBeInTheDocument()
  expect(client.analytics).toHaveBeenCalledTimes(2)
  expect(refreshUser).toHaveBeenCalledTimes(1)
})

it('keeps an expired session denied when the selected park changes', async () => {
  const client = { analytics: vi.fn().mockRejectedValue(new ApiError(401)) }
  const view = render(tree({ client }))
  await screen.findByRole('heading', { name: 'Сессия истекла' })
  view.rerender(tree({ client, selectedPark: otherPark }))
  expect(screen.getByRole('heading', { name: 'Сессия истекла' })).toBeVisible()
  expect(client.analytics).toHaveBeenCalledTimes(1)
})

it('keeps historical content during an offline background refresh and allows retry', async () => {
  const client = { analytics: vi.fn().mockResolvedValueOnce(fixture()).mockRejectedValueOnce(new TypeError('offline')).mockResolvedValue(fixture()) }
  render(tree({ client }))
  await screen.findByRole('region', { name: 'История парка Север' })
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 120_001)
  fireEvent.focus(window)
  await screen.findByText('Нет сети')
  expect(screen.getByRole('region', { name: 'История парка Север' })).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Повторить' }))
  await waitFor(() => expect(screen.queryByText('Нет сети')).not.toBeInTheDocument())
  expect(client.analytics).toHaveBeenCalledTimes(3)
})


it('keeps historical analytics idle until the user requests a refresh', async () => {
  vi.useFakeTimers()
  const client = { analytics: vi.fn(async () => fixture()) }
  render(tree({ client }))
  await act(async () => {})
  expect(screen.getByRole('region', { name: 'История парка Север' })).toBeVisible()
  await act(async () => { await vi.advanceTimersByTimeAsync(120_000) })
  expect(client.analytics).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByRole('button', { name: 'Обновить аналитику' }))
  await act(async () => {})
  expect(client.analytics).toHaveBeenCalledTimes(2)
  expect(screen.getByRole('region', { name: 'История парка Север' })).toBeVisible()
})


it.each([200, 403])('does not reuse a retired pending request after remount when it later returns %s', async status => {
  const pending = deferred<ReturnType<typeof fixture>>()
  const refreshUser = vi.fn(async () => makeUser())
  const client = { analytics: vi.fn().mockReturnValueOnce(pending.promise).mockResolvedValue(fixture()) }
  const first = render(tree({ client, refreshUser }))
  first.unmount()
  render(tree({ client, refreshUser }))
  await screen.findByRole('region', { name: 'История парка Север' })
  await act(async () => { if (status === 403) pending.reject(new ApiError(403)); else pending.resolve(fixture()) })
  expect(client.analytics).toHaveBeenCalledTimes(2)
  expect(refreshUser).not.toHaveBeenCalled()
  expect(screen.getByRole('region', { name: 'История парка Север' })).toBeVisible()
})

// Keep lifecycle assertions deterministic; pollingCapacity tests exercise jitter.
beforeEach(() => { vi.spyOn(Math, 'random').mockReturnValue(0) })


it.each(['admin', 'royal', 'operator'] as const)('shows every accessible active park for %s in all mode', async role => {
  const hiddenPark = { ...park, id: 9, name: 'Недоступный' }
  const inactivePark = { ...park, id: 10, name: 'Неактивный', is_active: false }
  const user = makeUser({ role, parks: [park, otherPark] })
  const client = { analytics: vi.fn(async (id: number) => fixture(id)) }
  render(tree({ client, user, selectedPark: null, allowAllParks: true, parks: [park, otherPark, hiddenPark, inactivePark], url: '/analytics?park=all&compare=8' }))
  expect(await screen.findByRole('table', { name: 'Сравнение парков' })).toBeVisible()
  expect(screen.getByRole('heading', { name: 'Все доступные парки' })).toBeVisible()
  expect(screen.queryByLabelText('Сравнить с парком')).not.toBeInTheDocument()
  expect(screen.getByLabelText('URL')).toHaveTextContent('?park=all')
  expect(screen.getByLabelText('URL')).not.toHaveTextContent('compare=')
  expect(client.analytics.mock.calls.map(call => call[0])).toEqual(role === 'operator' ? [7, 8] : [7, 8, 9])
  expect(screen.getByRole('region', { name: 'История парка Север' })).toBeVisible()
  expect(screen.getByRole('region', { name: 'История парка Юг' })).toBeVisible()
})

it('keeps available park history visible when another park temporarily fails', async () => {
  const client = { analytics: vi.fn(async (id: number) => {
    if (id === otherPark.id) throw new ApiError(503)
    return fixture(id)
  }) }
  render(tree({ client, selectedPark: null, allowAllParks: true, url: '/analytics?park=all' }))

  expect(await screen.findByRole('region', { name: 'История парка Север' })).toBeVisible()
  expect(screen.queryByRole('region', { name: 'История парка Юг' })).not.toBeInTheDocument()
  expect(screen.getByRole('alert')).toHaveTextContent('Юг')
  expect(screen.getByRole('alert')).toHaveTextContent('Не удалось загрузить историю')
  expect(client.analytics.mock.calls.map(call => call[0])).toEqual([7, 8])
})

it('loads the missing park after retrying a partial all-park result', async () => {
  let unavailable = true
  const client = { analytics: vi.fn(async (id: number) => {
    if (id === otherPark.id && unavailable) throw new ApiError(503)
    return fixture(id)
  }) }
  render(tree({ client, selectedPark: null, allowAllParks: true, url: '/analytics?park=all' }))
  expect(await screen.findByRole('region', { name: 'История парка Север' })).toBeVisible()
  expect(screen.getByRole('alert')).toHaveTextContent('Юг')

  unavailable = false
  fireEvent.click(screen.getByRole('button', { name: 'Повторить загрузку' }))
  expect(await screen.findByRole('region', { name: 'История парка Юг' })).toBeVisible()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

it('keeps the last complete all-park history during a failed refresh', async () => {
  let unavailable = false
  const client = { analytics: vi.fn(async (id: number) => {
    if (id === otherPark.id && unavailable) throw new ApiError(503)
    return fixture(id)
  }) }
  render(tree({ client, selectedPark: null, allowAllParks: true, url: '/analytics?park=all' }))
  expect(await screen.findByRole('region', { name: 'История парка Юг' })).toBeVisible()

  unavailable = true
  fireEvent.click(screen.getByRole('button', { name: 'Обновить аналитику' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Сервис временно недоступен')
  expect(screen.getByRole('region', { name: 'История парка Север' })).toBeVisible()
  expect(screen.getByRole('region', { name: 'История парка Юг' })).toBeVisible()
})

it('does not request any park for an operator with no assigned parks in all mode', () => {
  const client = { analytics: vi.fn(async (id: number) => fixture(id)) }
  render(tree({ client, user: makeUser({ role: 'operator', parks: [] }), selectedPark: null, allowAllParks: true }))
  expect(screen.getByRole('heading', { name: 'Нет доступных парков' })).toBeVisible()
  expect(client.analytics).not.toHaveBeenCalled()
})

it('bounds all-park loading to three requests and preserves park ordering', async () => {
  const parks = Array.from({ length: 5 }, (_, i) => ({ ...park, id: i + 7, name: `Парк ${i + 7}` }))
  const pending = parks.map(() => deferred<ReturnType<typeof fixture>>())
  const client = { analytics: vi.fn((id: number) => pending[id - 7].promise) }
  render(tree({ client, user: makeUser({ role: 'admin' }), parks, selectedPark: null, allowAllParks: true }))
  expect(client.analytics.mock.calls.map(call => call[0])).toEqual([7, 8, 9])
  await act(async () => pending[1].resolve(fixture(8)))
  expect(client.analytics.mock.calls.map(call => call[0])).toEqual([7, 8, 9, 10])
  await act(async () => pending[0].resolve(fixture(7)))
  expect(client.analytics.mock.calls.map(call => call[0])).toEqual([7, 8, 9, 10, 11])
  await act(async () => { pending.slice(2).forEach((item, i) => item.resolve(fixture(i + 9))) })
  expect(screen.getAllByRole('region', { name: /История парка/ }).map(item => item.getAttribute('aria-label'))).toEqual(parks.map(item => `История парка ${item.name}`))
})

it.each([200, 403])('stops queued all-park calls and ignores late %s responses when switching to one park', async status => {
  const parks = Array.from({ length: 5 }, (_, i) => ({ ...park, id: i + 7, name: `Парк ${i + 7}` }))
  const user = makeUser({ role: 'admin' })
  const refreshUser = vi.fn(async () => user)
  const pending = parks.slice(0, 3).map(() => deferred<ReturnType<typeof fixture>>())
  const client = { analytics: vi.fn((id: number) => id === 11 ? Promise.resolve(fixture(id)) : pending[id - 7].promise) }
  const view = render(tree({ client, user, refreshUser, parks, selectedPark: null, allowAllParks: true }))
  view.rerender(tree({ client, user, refreshUser, parks, selectedPark: parks[4], allowAllParks: true }))
  expect(client.analytics.mock.calls.map(call => call[0])).toEqual([7, 8, 9])
  await act(async () => { pending.forEach((item, i) => status === 403 ? item.reject(new ApiError(403)) : item.resolve(fixture(i + 7))) })
  await screen.findByRole('region', { name: 'История парка Парк 11' })
  expect(client.analytics.mock.calls.map(call => call[0])).toEqual([7, 8, 9, 11])
  expect(screen.getAllByRole('region', { name: /История парка/ })).toHaveLength(1)
  expect(refreshUser).not.toHaveBeenCalled()
})

it.each([401, 403])('stops queued all-park calls immediately after %s denial', async status => {
  const parks = Array.from({ length: 5 }, (_, i) => ({ ...park, id: i + 7 }))
  const pending = parks.slice(0, 3).map(() => deferred<ReturnType<typeof fixture>>())
  const client = { analytics: vi.fn((id: number) => pending[id - 7].promise) }
  render(tree({ client, user: makeUser({ role: 'admin' }), parks, selectedPark: null, allowAllParks: true }))
  await act(async () => { pending[1].reject(new ApiError(status)); pending[0].resolve(fixture(7)); pending[2].resolve(fixture(9)) })
  expect(await screen.findByRole('heading', { name: status === 401 ? 'Сессия истекла' : 'Нет доступа' })).toBeVisible()
  expect(client.analytics.mock.calls.map(call => call[0])).toEqual([7, 8, 9])
  expect(screen.queryByRole('region', { name: /История парка/ })).not.toBeInTheDocument()
})


it('loads all parks through StrictMode effect replay', async () => {
  const client = { analytics: vi.fn(async (id: number) => fixture(id)) }
  render(<StrictMode>{tree({ client, selectedPark: null, allowAllParks: true })}</StrictMode>)
  expect(await screen.findByRole('region', { name: 'История парка Север' })).toBeVisible()
  expect(screen.getAllByRole('region', { name: /История парка/ })).toHaveLength(2)
})

it('removes cached all-park history when a denial arrives after another park failed', async () => {
  const user = makeUser()
  const refreshUser = vi.fn(async () => user)
  const first = deferred<ReturnType<typeof fixture>>()
  const second = deferred<ReturnType<typeof fixture>>()
  let revalidating = false
  const client = { analytics: vi.fn((id: number) => revalidating ? (id === park.id ? first.promise : second.promise) : Promise.resolve(fixture(id))) }
  render(tree({ client, user, refreshUser, selectedPark: null, allowAllParks: true }))
  await screen.findByRole('region', { name: 'История парка Север' })
  revalidating = true
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 120_001)
  fireEvent.focus(window)
  await waitFor(() => expect(client.analytics).toHaveBeenCalledTimes(4))
  await act(async () => first.reject(new ApiError(500)))
  expect(screen.getByRole('region', { name: 'История парка Север' })).toBeVisible()
  await act(async () => second.reject(new ApiError(403)))
  expect(await screen.findByRole('heading', { name: 'Нет доступа' })).toBeVisible()
  expect(screen.queryByRole('region', { name: /История парка/ })).not.toBeInTheDocument()
  expect(refreshUser).toHaveBeenCalledTimes(1)
})
