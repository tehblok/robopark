import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api, type Report } from '../../api'
import type { ReportsApiClient } from '../../domains/reports/reports'
import { ReportDetail } from './ReportDetail'

const report: Report = {
  id: 42,
  kind: 'mechanic_problem',
  status: 'open',
  park_id: 7,
  author_user_id: 5,
  target_role: 'operator',
  tracker_key: null,
  tracker_url: null,
  title: 'Нужна диагностика',
  body: '',
  parent_report_id: null,
  return_comment: null,
  created_at: '2026-09-04T08:00:00Z',
  updated_at: '2026-09-04T08:00:00Z',
  resolved_at: null,
  attachments: [{
    id: 11,
    kind: 'device_photo',
    filename: 'robot.jpg',
    content_type: 'image/jpeg',
    size_bytes: 3,
  }],
}

describe('ReportDetail', () => {
  afterEach(() => vi.restoreAllMocks())

  it('requires confirmation before a manager permanently deletes a report', async () => {
    const actor = userEvent.setup()
    const reportDelete = vi.fn(async (_id: number) => undefined)
    const onDeleted = vi.fn()
    render(<ReportDetail
      apiClient={{ ...api, reportDelete } as ReportsApiClient}
      canAct={false}
      canDelete
      onClose={vi.fn()}
      onDeleted={onDeleted}
      onUpdated={vi.fn()}
      ownerKey="manager"
      report={report}
      showEscalate={false}
    />)

    await actor.click(screen.getByRole('button', { name: 'Удалить репорт' }))
    expect(screen.getByRole('alertdialog')).toBeVisible()
    expect(reportDelete).not.toHaveBeenCalled()
    await actor.type(screen.getByRole('textbox', { name: /Введите.*УДАЛИТЬ/ }), 'УДАЛИТЬ')
    await actor.click(screen.getByRole('button', { name: 'Удалить безвозвратно' }))
    expect(reportDelete).toHaveBeenCalledWith(42)
    expect(onDeleted).toHaveBeenCalledOnce()
  })

  it('explains why a campaign result cannot be deleted', async () => {
    const actor = userEvent.setup()
    const onDeleted = vi.fn()
    render(<ReportDetail
      apiClient={{ ...api, reportDelete: vi.fn(async () => { throw new ApiError(409, 'report_linked_to_campaign') }) } as ReportsApiClient}
      canAct={false} canDelete onClose={vi.fn()} onDeleted={onDeleted}
      onUpdated={vi.fn()} ownerKey="manager" report={report} showEscalate={false}
    />)
    await actor.click(screen.getByRole('button', { name: 'Удалить репорт' }))
    await actor.type(screen.getByRole('textbox', { name: /Введите.*УДАЛИТЬ/ }), 'УДАЛИТЬ')
    await actor.click(screen.getByRole('button', { name: 'Удалить безвозвратно' }))
    expect((await screen.findAllByText('Этот репорт связан с результатом СК/оклейки, поэтому удалить его нельзя.')).length).toBeGreaterThan(0)
    expect(onDeleted).not.toHaveBeenCalled()
  })

  it('exposes an authorized attachment download link for the selected report', () => {
    render(<ReportDetail
      canAct={false}
      onClose={vi.fn()}
      onUpdated={vi.fn()}
      ownerKey="owner-a"
      report={report}
      showEscalate={false}
    />)

    expect(screen.getByRole('link', { name: 'robot.jpg' }))
      .toHaveAttribute('href', '/api/reports/42/attachments/11')
  })

  it('does not deliver a late action result to a replacement owner', async () => {
    const actor = userEvent.setup()
    let resolve!: (value: Report) => void
    const pending = new Promise<Report>((onResolve) => { resolve = onResolve })
    const reportDone = vi.spyOn(api, 'reportDone').mockImplementation(() => pending)
    const apiClient = { ...api, reportDone } as ReportsApiClient
    const onUpdated = vi.fn()
    const view = render(<ReportDetail
      apiClient={apiClient}
      canAct
      onClose={vi.fn()}
      onUpdated={onUpdated}
      ownerKey="owner-a"
      report={report}
      showEscalate={false}
    />)

    await actor.click(screen.getByRole('button', { name: 'Готово' }))
    view.rerender(<ReportDetail
      apiClient={apiClient}
      canAct
      onClose={vi.fn()}
      onUpdated={onUpdated}
      ownerKey="owner-b"
      report={{ ...report, id: 84, title: 'Новый владелец' }}
      showEscalate={false}
    />)
    await act(async () => resolve({ ...report, status: 'done' }))

    expect(onUpdated).not.toHaveBeenCalled()
    expect(screen.queryByText('Действие выполнено.')).not.toBeInTheDocument()
  })

  it('lets the author edit and resubmit a returned report', async () => {
    const actor = userEvent.setup()
    const returned = { ...report, status: 'returned', return_comment: 'Add details' }
    const reportResubmit = vi.fn(async () => ({ ...returned, status: 'open' }))
    const apiClient = { ...api, reportResubmit } as ReportsApiClient

    render(<ReportDetail
      apiClient={apiClient}
      canAct={false}
      canResubmit
      onClose={vi.fn()}
      onUpdated={vi.fn()}
      ownerKey="owner-a"
      report={returned}
      showEscalate={false}
    />)

    await actor.clear(screen.getByRole('textbox', { name: 'Заголовок' }))
    await actor.type(screen.getByRole('textbox', { name: 'Заголовок' }), 'Уточнёно')
    await actor.click(screen.getByRole('button', { name: 'Повторно отправить' }))

    expect(reportResubmit).toHaveBeenCalledWith(42, expect.objectContaining({ title: 'Уточнёно' }))
  })
})
