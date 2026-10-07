import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { BotSettingsPanel } from './BotSettingsPanel'
import type { BotClient } from './botApi'

const status = {
  desired_enabled: false,
  runtime_state: 'unknown',
  token_configured: false,
  token_masked: null,
  token_updated_at: null,
  token_encrypted: false,
}

function client(): BotClient {
  return {
    getStatus: vi.fn().mockResolvedValue(status),
    setToken: vi.fn().mockResolvedValue({ ...status, token_configured: true, token_masked: '•••• (40)', token_encrypted: true }),
    setEnabled: vi.fn().mockRejectedValue(new Error('bot_host_control_unavailable')),
    previewImport: vi.fn().mockResolvedValue({ total_bytes: 12, files: [{ filename: 'roles.json', byte_count: 12, sha256: 'a'.repeat(64) }] }),
    executeImport: vi.fn().mockResolvedValue({ total_bytes: 12, files: [{ filename: 'roles.json', byte_count: 12, sha256: 'a'.repeat(64) }] }),
  }
}

describe('BotSettingsPanel', () => {
  it('can hide legacy ZIP import in the native settings flow', async () => {
    render(<BotSettingsPanel client={client()} showLegacyImport={false} />)
    await screen.findByText('Telegram-бот')
    expect(screen.queryByLabelText('Архив данных бота')).not.toBeInTheDocument()
    expect(screen.queryByText('Данные существующего бота')).not.toBeInTheDocument()
  })

  it('does not report a missing token or endless loading when status is unavailable', async () => {
    const api = client()
    api.getStatus = vi.fn().mockRejectedValueOnce(new Error('network unavailable')).mockResolvedValue(status)
    render(<BotSettingsPanel client={api} />)
    expect(await screen.findByRole('alert')).toBeVisible()
    expect(screen.queryByText('Проверяем…')).not.toBeInTheDocument()
    expect(screen.queryByText('Не задан')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Включить бота' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Обновить состояние' }))
    expect(await screen.findByText('Не задан')).toBeVisible()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('saves a token without displaying it and previews data before import', async () => {
    const api = client()
    render(<BotSettingsPanel client={api} />)
    expect(await screen.findByText('Telegram-бот')).toBeVisible()

    const token = screen.getByLabelText('Токен Telegram-бота')
    expect(token).toHaveAttribute('type', 'password')
    fireEvent.change(token, { target: { value: 'temporary-secret' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить токен' }))
    await waitFor(() => expect(api.setToken).toHaveBeenCalledWith('temporary-secret'))
    expect(await screen.findByText('Токен сохранён в Robopark.')).toBeVisible()
    expect(screen.queryByText('temporary-secret')).not.toBeInTheDocument()

    const upload = screen.getByLabelText('Архив данных бота')
    fireEvent.change(upload, { target: { files: [new File(['zip'], 'bot.zip', { type: 'application/zip' })] } })
    fireEvent.click(screen.getByRole('button', { name: 'Проверить архив' }))
    expect(await screen.findByText('roles.json')).toBeVisible()
    expect(api.executeImport).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Импортировать данные' }))
    await waitFor(() => expect(api.executeImport).toHaveBeenCalledTimes(1))
  })

  it('shows host control failure instead of a false running state', async () => {
    const api = client()
    api.getStatus = vi.fn().mockResolvedValue({ ...status, token_configured: true })
    render(<BotSettingsPanel client={api} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Включить бота' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('управление сервисом недоступно')
    expect(screen.getByText('Выключен')).toBeVisible()
  })
})
