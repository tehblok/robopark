import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../../../api'
import type { HostCapabilities, OtaUploadClient, SystemClient } from '../../../opsApi'
import { OtaUpdatePanel } from './OtaUpdatePanel'
import * as otaManifest from './otaManifest'
import * as otaUpload from './otaUpload'

const capabilities: HostCapabilities = {
  state: 'ready', generated_at: '2026-09-25T12:00:00Z', expires_at: '2099-09-25T12:05:00Z', revision: 'a'.repeat(64),
  operations: { 'ota-update': { available: true, unavailable_reason: null } } as HostCapabilities['operations'],
}
const system = {
  reauthorize: vi.fn(), startOperation: vi.fn(), getSummary: vi.fn(), getHistory: vi.fn(), getCapabilities: vi.fn(), getOperation: vi.fn(),
  getOperations: vi.fn(), getOperationContext: vi.fn(), operationArtifactUrl: vi.fn(),
} as SystemClient
const uploads = { list: vi.fn(), create: vi.fn(), offset: vi.fn(), append: vi.fn(), finalize: vi.fn(), remove: vi.fn() } as OtaUploadClient

afterEach(() => vi.restoreAllMocks())

describe('OtaUpdatePanel', () => {
  async function prepareVerifiedUpload() {
    vi.spyOn(otaManifest, 'inspectOtaFile').mockResolvedValue({
      format_version: 1, app_version: '0.2.0', compatible_from: ['0.2.0-rc.16'],
      required_free_bytes: 512 * 1024 ** 2, changes: [],
    })
    vi.spyOn(otaUpload, 'hashOtaFile').mockResolvedValue('a'.repeat(64))
    vi.spyOn(otaUpload, 'uploadOtaFile').mockResolvedValue({
      upload_id: '58d78531-6388-41e5-b0fb-2fdcf3df5032', filename: 'release.ota',
      size: 1, sha256: 'a'.repeat(64), offset: 1, expires_at: 1,
      state: 'verified', chunk_size: 1,
    })
    const props = { actor: { id: 1, username: 'royal' }, capabilities, currentVersion: '0.2.0-rc.16',
      onAccepted: vi.fn(), onPostingChange: vi.fn(), onRefreshCapabilities: vi.fn(), system, uploads }
    const view = render(<OtaUpdatePanel {...props} job={null} />)
    fireEvent.change(screen.getByLabelText('Выберите OTA-пакет'), { target: { files: [new File(['x'], 'release.ota')] } })
    fireEvent.click(await screen.findByRole('button', { name: 'Проверить на сервере' }))
    await screen.findByRole('button', { name: 'Установить 0.2.0' })
    return { ...view, props }
  }

  it('releases a staged upload and clears authorization before allowing another verification', async () => {
    let finish!: () => void
    vi.mocked(uploads.remove).mockImplementation(() => new Promise<void>(resolve => { finish = resolve }))
    await prepareVerifiedUpload()
    fireEvent.change(screen.getByLabelText('Пароль'), { target: { value: 'test-only' } })
    fireEvent.change(screen.getByLabelText('Код TOTP или восстановления'), { target: { value: 'test-only' } })
    fireEvent.click(screen.getByRole('button', { name: 'Удалить загруженный пакет' }))
    expect(uploads.remove).toHaveBeenCalledWith('58d78531-6388-41e5-b0fb-2fdcf3df5032')
    expect(screen.getByLabelText('Выберите OTA-пакет')).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Установить 0.2.0' })).not.toBeInTheDocument()
    finish()
    fireEvent.click(await screen.findByRole('button', { name: 'Проверить на сервере' }))
    await screen.findByRole('button', { name: 'Установить 0.2.0' })
    expect(screen.getByLabelText('Пароль')).toHaveValue('')
    expect(screen.getByLabelText('Код TOTP или восстановления')).toHaveValue('')
  })

  it('retains a staged upload when the server refuses removal of an in-use package', async () => {
    vi.mocked(uploads.remove).mockRejectedValue(new ApiError(400, 'ota_upload_in_use'))
    const { rerender, props } = await prepareVerifiedUpload()
    fireEvent.click(screen.getByRole('button', { name: 'Удалить загруженный пакет' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('уже используется')
    expect(screen.getByRole('button', { name: 'Удалить загруженный пакет' })).toBeEnabled()
    rerender(<OtaUpdatePanel {...props} job={{ id: 'in-flight', kind: 'ota-update', state: 'running', phase: 'verify', progress_percent: 0, error: null }} />)
    expect(screen.queryByRole('button', { name: 'Удалить загруженный пакет' })).not.toBeInTheDocument()
  })

  it('explains how to recover from an upload quota and keeps retry available', async () => {
    vi.spyOn(otaManifest, 'inspectOtaFile').mockResolvedValue({
      format_version: 1, app_version: '0.2.0', compatible_from: [], required_free_bytes: 1, changes: [],
    })
    vi.spyOn(otaUpload, 'hashOtaFile').mockResolvedValue('a'.repeat(64))
    vi.spyOn(otaUpload, 'uploadOtaFile').mockRejectedValue(new ApiError(409, 'ota_upload_quota'))
    render(<OtaUpdatePanel actor={{ id: 1, username: 'royal' }} capabilities={capabilities} job={null}
      onAccepted={vi.fn()} onPostingChange={vi.fn()} onRefreshCapabilities={vi.fn()} system={system} uploads={uploads} />)
    fireEvent.change(screen.getByLabelText('Выберите OTA-пакет'), { target: { files: [new File(['x'], 'release.ota')] } })
    fireEvent.click(await screen.findByRole('button', { name: 'Проверить на сервере' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('лимит загруженных пакетов')
    expect(screen.getByRole('alert')).toHaveTextContent('Показать мои загрузки')
    expect(screen.getByRole('button', { name: 'Проверить на сервере' })).toBeEnabled()
  })

  it('recovers quota by deleting an obsolete server upload without selecting its local file', async () => {
    vi.spyOn(otaManifest, 'inspectOtaFile').mockResolvedValue({
      format_version: 1, app_version: '0.2.1', compatible_from: ['0.2.0'], required_free_bytes: 1, changes: [],
    })
    vi.spyOn(otaUpload, 'hashOtaFile').mockResolvedValue('b'.repeat(64))
    const transfer = vi.spyOn(otaUpload, 'uploadOtaFile').mockRejectedValueOnce(new ApiError(409, 'ota_upload_quota'))
      .mockResolvedValue({ upload_id: 'new-upload', filename: 'new.ota', size: 1, sha256: 'b'.repeat(64), offset: 1, expires_at: 1, state: 'verified', chunk_size: 1 })
    vi.mocked(uploads.list).mockResolvedValue({ items: [{ upload_id: 'old-upload', filename: 'obsolete.ota', size: 1,
      sha256: 'a'.repeat(64), offset: 1, expires_at: 1, state: 'verified', chunk_size: 1, version: '0.2.0', compatible_from: ['0.2.0-rc.16'] }] })
    vi.mocked(uploads.remove).mockResolvedValue()
    render(<OtaUpdatePanel actor={{ id: 1, username: 'royal' }} currentVersion="0.2.0" capabilities={capabilities} job={null}
      onAccepted={vi.fn()} onPostingChange={vi.fn()} onRefreshCapabilities={vi.fn()} system={system} uploads={uploads} />)
    fireEvent.change(screen.getByLabelText('Выберите OTA-пакет'), { target: { files: [new File(['x'], 'new.ota')] } })
    fireEvent.click(await screen.findByRole('button', { name: 'Проверить на сервере' }))
    await screen.findByRole('alert')
    fireEvent.click(screen.getByRole('button', { name: 'Показать мои загрузки' }))
    fireEvent.click(await screen.findByRole('button', { name: /Удалить obsolete.ota/ }))
    await waitFor(() => expect(screen.queryByRole('button', { name: /Удалить obsolete.ota/ })).not.toBeInTheDocument())
    expect(uploads.remove).toHaveBeenCalledWith('old-upload')
    expect(screen.queryByRole('button', { name: 'Установить 0.2.0' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Проверить на сервере' }))
    expect(await screen.findByRole('button', { name: 'Установить 0.2.1' })).toBeVisible()
    expect(transfer).toHaveBeenCalledTimes(2)
    expect(otaManifest.inspectOtaFile).toHaveBeenCalledTimes(1)
  })

  it('shows a localized file picker while retaining the accessible OTA input', () => {
    render(<OtaUpdatePanel actor={{ id: 1, username: 'royal' }} capabilities={capabilities} job={null}
      onAccepted={vi.fn()} onPostingChange={vi.fn()} onRefreshCapabilities={vi.fn()} system={system} uploads={uploads} />)

    expect(screen.getByText('Выбрать файл')).toBeVisible()
    expect(screen.getByLabelText('Выберите OTA-пакет')).toHaveAttribute('type', 'file')
  })

  it('rejects a clean-install-only package locally before hashing or uploading it', async () => {
    vi.spyOn(otaManifest, 'inspectOtaFile').mockResolvedValue({
      format_version: 1, app_version: '0.2.0-rc.8', compatible_from: [],
      required_free_bytes: 512 * 1024 ** 2, changes: [],
    })
    const hash = vi.spyOn(otaUpload, 'hashOtaFile')
    render(<OtaUpdatePanel actor={{ id: 1, username: 'royal' }} capabilities={capabilities}
      currentVersion="0.2.0-rc.7" job={null} onAccepted={vi.fn()} onPostingChange={vi.fn()}
      onRefreshCapabilities={vi.fn()} system={system} uploads={uploads} />)

    fireEvent.change(screen.getByLabelText(/Выберите OTA-пакет/), { target: { files: [new File(['x'], 'release.ota')] } })

    expect(await screen.findByRole('alert')).toHaveTextContent('только для чистой установки')
    expect(hash).not.toHaveBeenCalled()
    expect(uploads.create).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: 'Проверить на сервере' })).not.toBeInTheDocument()
  })

  it('does not claim rollback when a failed host receipt has no rollback confirmation', () => {
    render(<OtaUpdatePanel actor={{ id: 1, username: 'royal' }} capabilities={capabilities} currentBuildId="old"
      job={{ id: 'job-1', kind: 'ota-update', state: 'failed', phase: 'publish', progress_percent: 100, error: 'ota_publish_failed' }}
      onAccepted={vi.fn()} onPostingChange={vi.fn()} onRefreshCapabilities={vi.fn()} system={system} uploads={uploads} />)
    expect(screen.getByRole('alert')).not.toHaveTextContent('откат')
  })

  it('explains a confirmed rollback and distinguishes failed recovery', () => {
    const props = { actor: { id: 1, username: 'royal' }, capabilities, currentBuildId: 'old',
      onAccepted: vi.fn(), onPostingChange: vi.fn(), onRefreshCapabilities: vi.fn(), system, uploads }
    const view = render(<OtaUpdatePanel {...props}
      job={{ id: 'job-rollback', kind: 'ota-update', state: 'failed', phase: 'failed', progress_percent: 100, error: 'ota_rolled_back' }} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Откат выполнен')

    view.rerender(<OtaUpdatePanel {...props}
      job={{ id: 'job-recovery', kind: 'ota-update', state: 'failed', phase: 'failed', progress_percent: 100, error: 'ota_rollback_failed' }} />)
    expect(screen.getByRole('alert')).toHaveTextContent('требуется ручное восстановление')
    expect(screen.getByRole('alert')).not.toHaveTextContent('Откат выполнен')
  })

  it('explains a disk refusal reported by the root host before snapshot', () => {
    render(<OtaUpdatePanel actor={{ id: 1, username: 'royal' }} capabilities={capabilities} currentBuildId="old"
      job={{ id: 'job-2', kind: 'ota-update', state: 'failed', phase: 'verified', progress_percent: 100, error: 'ota_insufficient_space' }}
      onAccepted={vi.fn()} onPostingChange={vi.fn()} onRefreshCapabilities={vi.fn()} system={system} uploads={uploads} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Недостаточно свободного места')
  })

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

  it('reports a deterministic disk preflight rejection without pretending the operation started', async () => {
    vi.spyOn(otaManifest, 'inspectOtaFile').mockResolvedValue({
      format_version: 1, app_version: '0.2.0-rc.7', compatible_from: [],
      required_free_bytes: 8 * 1024 ** 3, changes: [],
    })
    vi.spyOn(otaUpload, 'hashOtaFile').mockResolvedValue('a'.repeat(64))
    vi.spyOn(otaUpload, 'uploadOtaFile').mockResolvedValue({
      upload_id: '58d78531-6388-41e5-b0fb-2fdcf3df5032', filename: 'release.ota',
      size: 1, sha256: 'a'.repeat(64), offset: 1, expires_at: 1,
      state: 'verified', chunk_size: 1,
    })
    vi.mocked(system.reauthorize).mockResolvedValue({ token: '' } as never)
    vi.mocked(system.startOperation).mockRejectedValue(new ApiError(507, 'ota_insufficient_space'))
    const accepted = vi.fn()
    render(<OtaUpdatePanel actor={{ id: 1, username: 'royal' }} capabilities={capabilities} currentBuildId="old" job={null}
      onAccepted={accepted} onPostingChange={vi.fn()} onRefreshCapabilities={vi.fn()} system={system} uploads={uploads} />)

    fireEvent.change(screen.getByLabelText(/Выберите OTA-пакет/), { target: { files: [new File(['x'], 'release.ota')] } })
    fireEvent.click(await screen.findByRole('button', { name: 'Проверить на сервере' }))
    await screen.findByRole('button', { name: 'Установить 0.2.0-rc.7' })
    fireEvent.change(screen.getByLabelText(/Введите UPDATE ROBOPARK/), { target: { value: 'UPDATE ROBOPARK' } })
    fireEvent.change(screen.getByLabelText('Пароль'), { target: { value: 'x' } })
    fireEvent.change(screen.getByLabelText('Код TOTP или восстановления'), { target: { value: 'x' } })
    fireEvent.click(screen.getByRole('button', { name: 'Установить 0.2.0-rc.7' }))

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Недостаточно свободного места'))
    expect(accepted).not.toHaveBeenCalled()
  })

  it('asks to close terminal sessions when the host rejects OTA as terminal-active', async () => {
    const { props } = await prepareVerifiedUpload()
    vi.mocked(system.reauthorize).mockResolvedValue({ token: 'grant', expires_in: 120 })
    vi.mocked(system.startOperation).mockRejectedValue(new ApiError(409, 'terminal_active'))
    fireEvent.change(screen.getByLabelText(/Введите UPDATE ROBOPARK/), { target: { value: 'UPDATE ROBOPARK' } })
    fireEvent.change(screen.getByLabelText('Пароль'), { target: { value: 'x' } })
    fireEvent.change(screen.getByLabelText('Код TOTP или восстановления'), { target: { value: 'x' } })
    fireEvent.click(screen.getByRole('button', { name: 'Установить 0.2.0' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Завершите активные терминальные сессии')
    expect(props.onAccepted).not.toHaveBeenCalled()
  })

  it('shows a definite incompatibility refusal without inventing a queued operation', async () => {
    vi.spyOn(otaManifest, 'inspectOtaFile').mockResolvedValue({
      format_version: 1, app_version: '0.2.0-rc.8', compatible_from: [],
      required_free_bytes: 512 * 1024 ** 2, changes: [],
    })
    vi.spyOn(otaUpload, 'hashOtaFile').mockResolvedValue('a'.repeat(64))
    vi.spyOn(otaUpload, 'uploadOtaFile').mockResolvedValue({
      upload_id: '58d78531-6388-41e5-b0fb-2fdcf3df5032', filename: 'release.ota',
      size: 1, sha256: 'a'.repeat(64), offset: 1, expires_at: 1,
      state: 'verified', chunk_size: 1,
    })
    vi.mocked(system.reauthorize).mockResolvedValue({ token: '' } as never)
    vi.mocked(system.startOperation).mockRejectedValue(new ApiError(409, 'ota_incompatible'))
    const accepted = vi.fn()
    render(<OtaUpdatePanel actor={{ id: 1, username: 'royal' }} capabilities={capabilities} job={null}
      onAccepted={accepted} onPostingChange={vi.fn()} onRefreshCapabilities={vi.fn()} system={system} uploads={uploads} />)

    fireEvent.change(screen.getByLabelText(/Выберите OTA-пакет/), { target: { files: [new File(['x'], 'release.ota')] } })
    fireEvent.click(await screen.findByRole('button', { name: 'Проверить на сервере' }))
    await screen.findByRole('button', { name: 'Установить 0.2.0-rc.8' })
    fireEvent.change(screen.getByLabelText(/Введите UPDATE ROBOPARK/), { target: { value: 'UPDATE ROBOPARK' } })
    fireEvent.change(screen.getByLabelText('Пароль'), { target: { value: 'x' } })
    fireEvent.change(screen.getByLabelText('Код TOTP или восстановления'), { target: { value: 'x' } })
    fireEvent.click(screen.getByRole('button', { name: 'Установить 0.2.0-rc.8' }))

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('несовместим'))
    expect(accepted).not.toHaveBeenCalled()
  })
})
