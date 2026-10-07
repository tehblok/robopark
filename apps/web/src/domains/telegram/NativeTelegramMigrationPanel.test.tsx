import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '../../api'
import { NativeTelegramMigrationPanel } from './NativeTelegramMigrationPanel'
import type { NativeTelegramClient, TelegramMigrationPreview } from './nativeTelegramApi'

const preview: TelegramMigrationPreview = {
  fingerprint: 'a'.repeat(64),
  already_applied: false,
  park_updates: [{ park_id: 7, park_tag: 'north', location_key: 'north-depot', chat_id: -1001234567890, thread_id: 42 }],
  jobs: [{
    source_ref: 'schedules:hourly_report:north-depot', source: 'schedules', source_id: 'hourly_report:north-depot',
    park_id: 7, park_tag: 'north', title: 'Часовой отчёт', kind: 'report', schedule: 'hourly', time: null,
    weekdays: [0, 1, 2, 3, 4], start_hour: 9, end_hour: 18, text: null, url: null, tracker_tag: 'north',
    alternate: 'all', anchor_date: null,
  }],
  conflicts: [{ source: 'campaigns', source_id: 'legacy-campaign', park_tag: 'missing', reason: 'park_not_found' }],
  counts: { park_updates: 1, jobs: 1, conflicts: 1, skipped: 0 },
}

function client(): NativeTelegramClient {
  return {
    getAdmin: vi.fn(), updatePark: vi.fn(), createJob: vi.fn(), updateJob: vi.fn(), deleteJob: vi.fn(), runJob: vi.fn(),
    getAccount: vi.fn(), createLinkCode: vi.fn(), unlinkAccount: vi.fn(),
    getMigrationPreview: vi.fn().mockResolvedValue(structuredClone(preview)),
    applyMigration: vi.fn().mockResolvedValue({ ...structuredClone(preview), applied: true, applied_at: '2026-10-07T12:00:00Z' }),
  }
}

describe('NativeTelegramMigrationPanel', () => {
  it('explains conflicting legacy folders and allows a fresh preview after the source is fixed', async () => {
    const api = client()
    vi.mocked(api.getMigrationPreview)
      .mockRejectedValueOnce(new ApiError(409, 'legacy_bot_sources_conflict'))
      .mockResolvedValueOnce(structuredClone(preview))
    render(<NativeTelegramMigrationPanel client={api} onApplied={vi.fn()} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('В двух старых папках')
    expect(api.applyMigration).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Обновить предпросмотр' }))
    expect(await screen.findByRole('heading', { name: 'Совпавшие чаты парков' })).toBeVisible()
    expect(api.getMigrationPreview).toHaveBeenCalledTimes(2)
  })

  it('previews exact park-tag matches and applies disabled jobs with the fingerprint once', async () => {
    const api = client()
    const onApplied = vi.fn().mockResolvedValue(undefined)
    render(<NativeTelegramMigrationPanel client={api} onApplied={onApplied} />)

    await screen.findByRole('heading', { name: 'Совпавшие чаты парков' })
    const rows = screen.getAllByRole('listitem')
    expect(rows.some(row => row.textContent?.includes('north ← north-depot: чат -1001234567890'))).toBe(true)
    expect(rows.some(row => row.textContent?.includes('Часовой отчёт — парк north, каждый час 09:00–18:00, выключено'))).toBe(true)
    expect(rows.some(row => row.textContent?.includes('кампании legacy-campaign, парк missing: парк с таким тегом не найден'))).toBe(true)
    expect(screen.getByText(/Существующие чаты не перезаписываются/)).toBeVisible()
    const apply = screen.getByRole('button', { name: 'Перенести 1 выключенных заданий' })
    fireEvent.click(apply)

    await waitFor(() => expect(api.applyMigration).toHaveBeenCalledWith(preview.fingerprint))
    expect(onApplied).toHaveBeenCalledOnce()
    expect(await screen.findByText('Перенос выполнен: создано 1 выключенных заданий.')).toBeVisible()
    expect(apply).toBeDisabled()
  })

  it('reloads the preview once after a fingerprint conflict and requires another explicit apply', async () => {
    const api = client()
    const fresh = { ...structuredClone(preview), fingerprint: 'b'.repeat(64), jobs: [], counts: { ...preview.counts, jobs: 0 } }
    vi.mocked(api.getMigrationPreview).mockResolvedValueOnce(structuredClone(preview)).mockResolvedValueOnce(fresh)
    vi.mocked(api.applyMigration).mockRejectedValueOnce(new ApiError(409, 'migration_fingerprint_changed'))
    render(<NativeTelegramMigrationPanel client={api} onApplied={vi.fn()} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Перенести 1 выключенных заданий' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Предпросмотр обновлён')
    expect(api.getMigrationPreview).toHaveBeenCalledTimes(2)
    expect(api.applyMigration).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Перенести 0 выключенных заданий' })).toBeEnabled()
  })
})
