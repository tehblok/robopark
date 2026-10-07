import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { TelegramRuntimePanel } from './TelegramRuntimePanel'

const initial = { queries_paused: false, deliveries_paused: true, revision: 4 }
function setup() {
  return {
    control: vi.fn().mockResolvedValue(initial),
    usage: vi.fn().mockResolvedValue([{ user_id: 3, username: 'Механик', telegram_user_id: 7, stats: { today: 2, month: 10, total: 40 } }]),
    update: vi.fn().mockResolvedValue({ ...initial, queries_paused: true, revision: 5 }),
  }
}

describe('TelegramRuntimePanel', () => {
  it('royal pauses queries without resuming deliveries and sends the current revision', async () => {
    const client = setup()
    render(<TelegramRuntimePanel royal client={client} />)
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Пауза запросов сотрудников' }))
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить паузу' }))
    await screen.findByText('Настройки паузы сохранены.')
    expect(client.update).toHaveBeenCalledWith({ ...initial, queries_paused: true })
    expect(screen.getByRole('checkbox', { name: 'Пауза рассылок' })).toBeChecked()
    expect(screen.getByText('Механик')).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: '40' })).toBeInTheDocument()
  })

  it('admin can inspect usage and pause state but cannot change global flags', async () => {
    render(<TelegramRuntimePanel royal={false} client={setup()} />)
    await screen.findByText('Механик')
    expect(screen.getByRole('checkbox', { name: 'Пауза запросов сотрудников' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Сохранить паузу' })).not.toBeInTheDocument()
  })

  it('failed load remains recoverable without enabling unsaved controls', async () => {
    const client = setup()
    client.control.mockRejectedValueOnce(new Error('offline'))
    render(<TelegramRuntimePanel royal client={client} />)
    await screen.findByRole('alert')
    expect(screen.queryByRole('button', { name: 'Сохранить паузу' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Обновить статистику и паузу' }))
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
    expect(screen.getByRole('checkbox', { name: 'Пауза рассылок' })).toBeChecked()
  })
})
