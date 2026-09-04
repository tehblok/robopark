import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { Report } from '../../api'
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
  it('exposes an authorized attachment download link for the selected report', () => {
    render(<ReportDetail
      canAct={false}
      onClose={vi.fn()}
      onUpdated={vi.fn()}
      report={report}
      showEscalate={false}
    />)

    expect(screen.getByRole('link', { name: 'robot.jpg' }))
      .toHaveAttribute('href', '/api/reports/42/attachments/11')
  })
})
