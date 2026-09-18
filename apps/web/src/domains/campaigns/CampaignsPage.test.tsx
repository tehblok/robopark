import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, type CampaignDetail, type Park, type User } from '../../api'
import { ParkScopeContext } from '../../app/park/parkScope'
import { AuthContext } from '../../auth-context'
import { CampaignsPage } from './CampaignsPage'

const park: Park = { id: 7, name: 'Север', tag: 'NORTH', tracker_queue: 'ROBOPARK', is_active: true }
const user: User = { id: 2, username: 'worker', role: 'mechanic', access_status: 'approved', permissions: [], parks: [park] }
const detail: CampaignDetail = {
  id: 4, kind: 'service_company', name: 'СК Альфа', tracker_tag: 'service-2026', starts_on: '2026-09-01', due_on: '2026-09-30', is_active: true,
  park_ids: [7], park_names: ['Север'], total_count: 2, completed_count: 1, pending_review_count: 1, remaining_count: 1, percent_complete: 50, overdue: false,
  open_tickets: [
    { key: 'RP-1', summary: 'Заменить корпус', status: 'Открыт', park_id: 7, park_name: 'Север', robot: 'A101', url: 'https://tracker/RP-1', completed_at: null, completed_by: null, comment: null, report_id: null, review_status: null, tracker_transition: null },
  ],
  closed_tickets: [
    { key: 'RP-2', summary: 'Готово', status: 'Проверка', park_id: 7, park_name: 'Север', robot: 'A102', url: 'https://tracker/RP-2', completed_at: '2026-09-10T08:00:00Z', completed_by: 2, comment: 'Сделано', report_id: 9, review_status: 'open', tracker_transition: 'review' },
  ],
}

function renderPage(apiClient: Pick<typeof api, 'campaigns' | 'campaign' | 'refreshCampaign' | 'deleteCampaign' | 'createCampaign' | 'updateCampaign' | 'completeCampaignTicket'>, currentUser: User = user) {
  return render(<MemoryRouter initialEntries={['/campaigns/4']}><AuthContext.Provider value={{ user: currentUser, loading: false, login: vi.fn(), logout: vi.fn(), refreshUser: vi.fn() }}><ParkScopeContext.Provider value={{ allowAllParks: false, parkId: 7, selectedPark: park, parks: [park], loading: false, locked: true, setParkId: vi.fn(), refreshParks: vi.fn() }}><Routes><Route element={<CampaignsPage apiClient={apiClient} />} path="/campaigns/:campaignId" /></Routes></ParkScopeContext.Provider></AuthContext.Provider></MemoryRouter>)
}

function renderList(apiClient: Pick<typeof api, 'campaigns' | 'campaign' | 'refreshCampaign' | 'deleteCampaign' | 'createCampaign' | 'updateCampaign' | 'completeCampaignTicket'>) {
  return render(<MemoryRouter initialEntries={['/campaigns']}><AuthContext.Provider value={{ user, loading: false, login: vi.fn(), logout: vi.fn(), refreshUser: vi.fn() }}><ParkScopeContext.Provider value={{ allowAllParks: false, parkId: 7, selectedPark: park, parks: [park], loading: false, locked: true, setParkId: vi.fn(), refreshParks: vi.fn() }}><Routes><Route element={<CampaignsPage apiClient={apiClient} />} path="/campaigns" /></Routes></ParkScopeContext.Provider></AuthContext.Provider></MemoryRouter>)
}

function useViewport(matches: boolean) {
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({
    matches,
    media: '(max-width: 599px)',
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }))
}

beforeEach(() => useViewport(false))
afterEach(() => vi.unstubAllGlobals())

it('keeps mobile campaign search and ticket summary visible while deferring metrics and history', async () => {
  useViewport(true)
  renderPage({ ...api, campaign: vi.fn(async () => detail) })

  expect(await screen.findByRole('textbox', { name: 'Поиск по роботу' })).toBeVisible()
  expect(screen.getByText('A101')).toBeVisible()
  expect(screen.queryByRole('img', { name: 'СК Альфа: 50%' })).not.toBeInTheDocument()
  expect(screen.queryByText('A102')).not.toBeInTheDocument()
  expect(screen.queryByRole('textbox', { name: 'Комментарий для оператора' })).not.toBeInTheDocument()

  await userEvent.click(screen.getByRole('button', { name: 'Метрики' }))
  expect(screen.getByRole('img', { name: 'СК Альфа: 50%' })).toBeVisible()
})

it('labels active, completed and overdue campaigns with accessible text', async () => {
  const campaigns = [
    { ...detail, id: 1, name: 'Активная', is_active: true, overdue: false },
    { ...detail, id: 2, name: 'Завершённая', is_active: false, overdue: false },
    { ...detail, id: 3, name: 'Просроченная', is_active: true, overdue: true },
  ]
  renderList({ ...api, campaigns: vi.fn(async () => campaigns) })

  expect(await screen.findByText('Активна')).toBeVisible()
  expect(screen.getByText('Завершена')).toBeVisible()
  expect(screen.getByText('Просрочена')).toBeVisible()
  expect(screen.getAllByText('Сервисная компания')).toHaveLength(3)
})

it('shows progress, open and closed campaign tickets and filters by robot', async () => {
  const apiClient = { ...api, campaign: vi.fn(async () => detail) }
  renderPage(apiClient)
  expect(await screen.findByRole('heading', { name: 'СК Альфа' })).toBeVisible()
  expect(screen.getByRole('img', { name: 'СК Альфа: 50%' })).toBeVisible()
  expect(screen.getByText('A101')).toBeVisible()
  expect(screen.getByText('A102')).toBeVisible()
  await userEvent.type(screen.getByRole('textbox', { name: 'Поиск по роботу' }), 'missing')
  expect(screen.queryByText('A101')).not.toBeInTheDocument()
  expect(screen.getByText('Открытые тикеты не найдены.')).toBeVisible()
})

it('explains that a locally completed ticket is still waiting for Tracker', async () => {
  const waiting = { ...detail, closed_tickets: [{ ...detail.closed_tickets[0], tracker_transition: 'pending' }] }
  renderPage({ ...api, campaign: vi.fn(async () => waiting) })
  expect(await screen.findByText('Отправка в Tracker ожидается')).toBeVisible()
  expect(screen.getByRole('button', { name: 'Обновить из Tracker' })).toBeVisible()
})

it('keeps the previous campaign visible when a refresh fails', async () => {
  const campaign = vi.fn().mockResolvedValueOnce(detail).mockRejectedValueOnce(new Error('Tracker offline'))
  renderPage({ ...api, campaign, refreshCampaign: vi.fn(async () => ({ snapshot_state: 'pending', snapshot_at: null })) })
  expect(await screen.findByRole('heading', { name: 'СК Альфа' })).toBeVisible()
  await userEvent.click(screen.getByRole('button', { name: 'Обновить из Tracker' }))
  expect(await screen.findByText(/Показаны последние полученные данные/)).toBeVisible()
  expect(screen.getByRole('heading', { name: 'СК Альфа' })).toBeVisible()
})

it('requests a coalesced Tracker refresh and shows saved snapshot age while it runs', async () => {
  const pending = { ...detail, snapshot_state: 'pending' as const, snapshot_at: '2026-09-10T08:00:00Z' }
  const campaign = vi.fn(async () => pending)
  const refreshCampaign = vi.fn(async () => ({ snapshot_state: 'pending', snapshot_at: pending.snapshot_at }))
  renderPage({ ...api, campaign, refreshCampaign })
  expect(await screen.findByText(/Последнее обновление/)).toBeVisible()
  await userEvent.click(screen.getByRole('button', { name: 'Обновить из Tracker' }))
  await waitFor(() => expect(refreshCampaign).toHaveBeenCalledWith(4))
  expect(screen.getByText(/Обновление запрошено/)).toBeVisible()
})

it('lets a manager confirm campaign deletion and removes the detail view', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  const deleteCampaign = vi.fn(async () => ({ result: 'archived' as const }))
  renderPage({ ...api, campaign: vi.fn(async () => detail), deleteCampaign }, { ...user, role: 'royal' })
  expect(await screen.findByRole('heading', { name: 'СК Альфа' })).toBeVisible()
  await userEvent.click(screen.getByRole('button', { name: 'Удалить кампанию' }))
  await waitFor(() => expect(deleteCampaign).toHaveBeenCalledWith(4))
  expect(screen.queryByRole('heading', { name: 'СК Альфа' })).not.toBeInTheDocument()
})

it('sends a comment and photo to operator review', async () => {
  const complete = vi.fn(async () => ({ id: 1, issue_key: 'RP-1', report_id: 10, review_status: 'open', tracker_transition: 'review', completed_at: '2026-09-10T10:00:00Z' }))
  const campaign = vi.fn(async () => detail)
  const apiClient = { ...api, campaign, completeCampaignTicket: complete }
  renderPage(apiClient)
  const openPanel = (await screen.findByRole('heading', { name: 'Открытые · 1' })).closest('section')!
  await userEvent.click(within(openPanel).getByRole('button', { name: 'Заполнить и отправить на проверку' }))
  await userEvent.type(within(openPanel).getByRole('textbox', { name: 'Комментарий для оператора' }), 'Всё готово')
  const photo = new File(['photo'], 'done.jpg', { type: 'image/jpeg' })
  await userEvent.upload(within(openPanel).getByLabelText('Фото'), photo)
  fireEvent.submit(within(openPanel).getByRole('button', { name: 'Отправить оператору' }).closest('form')!)
  await waitFor(() => expect(complete).toHaveBeenCalledWith(4, 'RP-1', 7, 'Всё готово', photo, expect.any(String)))
  expect(campaign).toHaveBeenCalledTimes(2)
})

it('keeps only one ticket completion form open on a phone', async () => {
  useViewport(true)
  const twoOpen = {
    ...detail,
    open_tickets: [
      detail.open_tickets[0],
      { ...detail.open_tickets[0], key: 'RP-3', robot: 'A103' },
    ],
  }
  renderPage({ ...api, campaign: vi.fn(async () => twoOpen) })
  const actions = await screen.findAllByRole('button', { name: 'Заполнить и отправить на проверку' })

  await userEvent.click(actions[0])
  expect(screen.getAllByRole('textbox', { name: 'Комментарий для оператора' })).toHaveLength(1)
  await userEvent.click(screen.getByRole('button', { name: 'Заполнить и отправить на проверку' }))

  expect(screen.getAllByRole('textbox', { name: 'Комментарий для оператора' })).toHaveLength(1)
  expect(screen.getByRole('textbox', { name: 'Комментарий для оператора' }).closest('article'))
    .toHaveTextContent('A103')
})
