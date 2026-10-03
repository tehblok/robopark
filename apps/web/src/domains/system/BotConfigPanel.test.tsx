import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { BotConfigPanel } from './BotConfigPanel'
import type { BotConfigClient, BotConfigDocument } from './botConfigApi'

const revision = 'a'.repeat(64)
const document: BotConfigDocument = {
  sections: {
    roles: { revision, value: { roles: [] } },
    users: { revision, value: { users: [] } },
    locations: { revision, value: { locations: {} } },
    schedules: { revision, value: { jobs: [] } },
    broadcasts: { revision, value: { campaigns: [] } },
    campaigns: { revision, value: { campaigns: [] } },
    auxiliary_tracker_queues: { revision, value: [] },
    profile: { revision, value: 'prod' },
    dispatcher_pause: { revision, value: false },
    send_pause: { revision, value: false },
  },
}

function client(): BotConfigClient {
  return {
    read: vi.fn().mockResolvedValue(document),
    update: vi.fn().mockImplementation(async (_section, value) => ({ revision: 'b'.repeat(64), value })),
  }
}

describe('BotConfigPanel', () => {
  it('stops the loading message after a read failure and recovers on refresh', async () => {
    const api = client()
    api.read = vi.fn().mockRejectedValueOnce(new Error('network unavailable')).mockResolvedValue(document)
    render(<BotConfigPanel client={api} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось загрузить настройки')
    expect(screen.queryByText('Загружаем настройки…')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Обновить настройки' }))
    expect(await screen.findByRole('button', { name: 'Приостановить отправку' })).toBeVisible()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows the same operational controls as Telegram and saves with revision', async () => {
    const api = client()
    render(<BotConfigPanel client={api} />)
    expect(await screen.findByText('Настройки бота')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Приостановить отправку' }))
    await waitFor(() => expect(api.update).toHaveBeenCalledWith('send_pause', true, revision))
    expect(await screen.findByText('Отправка приостановлена')).toBeVisible()
  })

  it('does not silently overwrite a conflicting change from Telegram', async () => {
    const api = client()
    api.update = vi.fn().mockRejectedValue(new Error('stale_revision'))
    render(<BotConfigPanel client={api} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Приостановить диспетчер' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('изменились в Telegram или другой вкладке')
    expect(screen.getByRole('button', { name: 'Приостановить диспетчер' })).toBeVisible()
  })

  it('edits extra Tracker queues and send window through labelled controls', async () => {
    const api = client()
    const source = structuredClone(document)
    source.sections.schedules.value = {
      timezone: 'Europe/Moscow', planner_anchor: '2026-08-07',
      send_window: { start_hour: 9, end_hour: 21, enabled: true }, jobs: [],
    }
    api.read = vi.fn().mockResolvedValue(source)
    render(<BotConfigPanel client={api} />)

    fireEvent.click(await screen.findByText('Дополнительные очереди Tracker'))
    fireEvent.change(screen.getByRole('textbox', { name: 'Очереди Tracker через запятую' }), { target: { value: 'ROBOMAINT, SDCWH' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить дополнительные очереди' }))
    await waitFor(() => expect(api.update).toHaveBeenCalledWith('auxiliary_tracker_queues', ['ROBOMAINT', 'SDCWH'], revision))

    fireEvent.click(screen.getByText('Расписание и окно отправки'))
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Начало отправки, час' }), { target: { value: '10' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить окно отправки' }))
    await waitFor(() => expect(api.update).toHaveBeenCalledWith('schedules', expect.objectContaining({
      send_window: { start_hour: 10, end_hour: 21, enabled: true },
    }), revision))
  })

  it('shows campaigns and schedule jobs as controls without opening JSON', async () => {
    const api = client()
    const source = structuredClone(document)
    source.sections.broadcasts.value = { version: 1, removed_ids: [], campaigns: [{
      id: 'morning', label: 'Утро', text: 'Проверить парк', fire_at: '09:00',
      repeat: 'daily', location_mode: 'all', enabled: true,
    }] }
    api.read = vi.fn().mockResolvedValue(source)
    render(<BotConfigPanel client={api} />)

    fireEvent.click((await screen.findAllByText('Рассылки'))[0])
    fireEvent.click(screen.getByRole('checkbox', { name: 'Рассылка Утро включена' }))
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить рассылки' }))

    await waitFor(() => expect(api.update).toHaveBeenCalledWith('broadcasts', expect.objectContaining({
      campaigns: [expect.objectContaining({ id: 'morning', enabled: false })],
    }), revision))
  })
})
