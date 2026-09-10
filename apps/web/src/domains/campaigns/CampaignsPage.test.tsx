import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { expect, it, vi } from 'vitest'
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

function renderPage(apiClient: Pick<typeof api, 'campaigns' | 'campaign' | 'createCampaign' | 'updateCampaign' | 'completeCampaignTicket'>) {
  return render(<MemoryRouter initialEntries={['/campaigns/4']}><AuthContext.Provider value={{ user, loading: false, login: vi.fn(), logout: vi.fn(), refreshUser: vi.fn() }}><ParkScopeContext.Provider value={{ allowAllParks: false, parkId: 7, selectedPark: park, parks: [park], loading: false, locked: true, setParkId: vi.fn(), refreshParks: vi.fn() }}><Routes><Route element={<CampaignsPage apiClient={apiClient} />} path="/campaigns/:campaignId" /></Routes></ParkScopeContext.Provider></AuthContext.Provider></MemoryRouter>)
}

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
  await waitFor(() => expect(complete).toHaveBeenCalledWith(4, 'RP-1', 7, 'Всё готово', photo))
  expect(campaign).toHaveBeenCalledTimes(2)
})
