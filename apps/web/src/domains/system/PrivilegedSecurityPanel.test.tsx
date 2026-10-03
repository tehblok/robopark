import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PrivilegedSecurityPanel } from './PrivilegedSecurityPanel'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('PrivilegedSecurityPanel', () => {
  it('lets an unenrolled owner configure TOTP and receive recovery codes', async () => {
    const client = {
      status: vi.fn(async () => ({ enrolled: false })),
      beginEnrollment: vi.fn(async () => ({ secret: 'JBSWY3DPEHPK3PXP' })),
      confirmEnrollment: vi.fn(async () => ({ recovery_codes: ['AAAA-BBBB', 'CCCC-DDDD'] })),
    }
    render(<PrivilegedSecurityPanel client={client} username="owner" />)

    fireEvent.click(await screen.findByRole('button', { name: 'Настроить TOTP' }))
    expect(await screen.findByRole('img', { name: 'QR для настройки TOTP' })).toBeVisible()
    expect(screen.getByText('JBSWY3DPEHPK3PXP')).toBeVisible()
    fireEvent.change(screen.getByLabelText('Текущий пароль'), { target: { value: 'secret' } })
    fireEvent.change(screen.getByLabelText('Код из приложения'), { target: { value: '123456' } })
    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить настройку' }))

    await waitFor(() => expect(client.confirmEnrollment).toHaveBeenCalledWith({ password: 'secret', code: '123456' }))
    expect(await screen.findByText('AAAA-BBBB')).toBeVisible()
    expect(screen.getByText('CCCC-DDDD')).toBeVisible()
    expect(screen.getByText(/показываются один раз/i)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Скачать коды' })).toBeVisible()
    const create = vi.fn((blob: Blob) => {
      expect(blob.type).toBe('text/plain;charset=utf-8')
      return 'blob:recovery-codes'
    })
    const revoke = vi.fn()
    vi.stubGlobal('URL', class extends URL { static createObjectURL = create; static revokeObjectURL = revoke })
    const downloaded: string[] = []
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { downloaded.push(this.download) })
    fireEvent.click(screen.getByRole('button', { name: 'Скачать коды' }))
    expect(create).toHaveBeenCalledWith(expect.any(Blob))
    expect(await create.mock.calls[0][0].text()).toBe('Robopark — коды восстановления\n\nAAAA-BBBB\nCCCC-DDDD\n')
    expect(downloaded).toEqual(['robopark-recovery-codes.txt'])
    await waitFor(() => expect(revoke).toHaveBeenCalledWith('blob:recovery-codes'))
    fireEvent.click(screen.getByRole('button', { name: 'Я сохранил коды' }))
    expect(screen.queryByText('AAAA-BBBB')).not.toBeInTheDocument()
    expect(screen.getByText('TOTP настроен')).toBeVisible()
  })

  it('shows the enrolled state without exposing setup controls', async () => {
    const client = {
      status: vi.fn(async () => ({ enrolled: true })),
      beginEnrollment: vi.fn(),
      confirmEnrollment: vi.fn(),
    }
    render(<PrivilegedSecurityPanel client={client} username="owner" />)
    expect(await screen.findByText('TOTP настроен')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Настроить TOTP' })).not.toBeInTheDocument()
  })

  it('does not offer enrollment when TOTP status cannot be checked', async () => {
    const client = {
      status: vi.fn().mockRejectedValueOnce(new Error('unavailable')).mockResolvedValueOnce({ enrolled: true }),
      beginEnrollment: vi.fn(),
      confirmEnrollment: vi.fn(),
    }
    render(<PrivilegedSecurityPanel client={client} username="owner" />)
    expect(await screen.findByRole('button', { name: 'Повторить проверку TOTP' })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Настроить TOTP' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Повторить проверку TOTP' }))
    expect(await screen.findByText('TOTP настроен')).toBeVisible()
  })
})
