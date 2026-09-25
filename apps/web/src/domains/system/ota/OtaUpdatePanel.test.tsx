import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { HostCapabilities, OtaUploadClient, SystemClient } from '../../../opsApi'
import { OtaUpdatePanel } from './OtaUpdatePanel'

const capabilities: HostCapabilities = {
  state: 'ready', generated_at: '2026-09-25T12:00:00Z', expires_at: '2099-09-25T12:05:00Z', revision: 'a'.repeat(64),
  operations: { 'ota-update': { available: true, unavailable_reason: null } } as HostCapabilities['operations'],
}
const system = {
  reauthorize: vi.fn(), startOperation: vi.fn(), getSummary: vi.fn(), getHistory: vi.fn(), getCapabilities: vi.fn(), getOperation: vi.fn(),
} as SystemClient
const uploads = { create: vi.fn(), offset: vi.fn(), append: vi.fn(), finalize: vi.fn(), remove: vi.fn() } as OtaUploadClient

describe('OtaUpdatePanel', () => {
  it('keeps the destructive action unavailable until a valid OTA file passes local checks', async () => {
    render(<OtaUpdatePanel actor={{ id: 1, username: 'royal' }} capabilities={capabilities} currentBuildId="old" job={null}
      onAccepted={vi.fn()} onPostingChange={vi.fn()} onRefreshCapabilities={vi.fn()} system={system} uploads={uploads} />)
    expect(screen.getByRole('region', { name: 'OTA-обновление' })).toBeVisible()
    expect(screen.queryByRole('button', { name: /Установить/ })).not.toBeInTheDocument()
    const input = screen.getByLabelText(/Выберите OTA-пакет/)
    fireEvent.change(input, { target: { files: [new File(['bad'], 'release.zip')] } })
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('ota_filename_invalid'))
    expect(uploads.create).not.toHaveBeenCalled()
    expect(system.startOperation).not.toHaveBeenCalled()
  })
})
