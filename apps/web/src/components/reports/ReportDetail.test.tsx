import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type Report } from '../../api'
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
})
