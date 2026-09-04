import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type Report } from '../../api'
import type { ReportsApiClient } from '../../domains/reports/reports'
import { ReportForms } from './ReportForms'

const created: Report = {
  id: 42, kind: 'mechanic_problem', status: 'open', park_id: 7, author_user_id: 1,
  target_role: 'operator', tracker_key: null, tracker_url: null, title: 'Проблема', body: '',
  parent_report_id: null, return_comment: null, created_at: '2026-09-04T08:00:00Z',
  updated_at: '2026-09-04T08:00:00Z', resolved_at: null,
}

describe('ReportForms', () => {
  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  function form(overrides: Partial<React.ComponentProps<typeof ReportForms>> = {}) {
    return <ReportForms
      onCreated={vi.fn()}
      ownerKey="owner-a"
      parkId={7}
      principalId={1}
      {...overrides}
    />
  }

  function client(overrides: Partial<ReportsApiClient>): ReportsApiClient {
    return { ...api, ...overrides }
  }

  function deferred<T>() {
    let resolve!: (value: T) => void
    let reject!: (reason: unknown) => void
    const promise = new Promise<T>((onResolve, onReject) => {
      resolve = onResolve
      reject = onReject
    })
    return { promise, resolve, reject }
  }

  it('retries an attachment on the already-created report without posting the report again', async () => {
    const actor = userEvent.setup()
    const create = vi.spyOn(api, 'createReport').mockResolvedValue(created)
    const attach = vi.spyOn(api, 'reportAttach').mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({ id: 11, kind: 'device_photo', filename: 'robot.jpg', content_type: 'image/jpeg', size_bytes: 3 })
    render(form())

    await actor.click(screen.getByRole('button', { name: 'Проблема' }))
    await actor.type(screen.getByRole('textbox', { name: 'Заголовок *' }), 'Проблема')
    await actor.click(screen.getByRole('button', { name: 'Создать' }))
    expect(await screen.findByText('Репорт отправлен оператору.')).toBeVisible()

    await actor.upload(screen.getByLabelText('Файл'), new File(['jpg'], 'robot.jpg', { type: 'image/jpeg' }))
    await actor.click(screen.getByRole('button', { name: 'Прикрепить файл' }))
    expect(await screen.findByRole('alert')).toBeVisible()
    await actor.click(screen.getByRole('button', { name: 'Прикрепить файл' }))

    await waitFor(() => expect(attach).toHaveBeenCalledTimes(2))
    expect(create).toHaveBeenCalledTimes(1)
    expect(attach).toHaveBeenLastCalledWith(42, 'device_photo', expect.any(File))
  })

  it('accepts mobile image filenames when their MIME type is omitted or generic', async () => {
    const actor = userEvent.setup()
    vi.spyOn(api, 'createReport').mockResolvedValue(created)
    const attach = vi.spyOn(api, 'reportAttach').mockResolvedValue({
      id: 11, kind: 'device_photo', filename: 'robot.heic', content_type: 'image/heic', size_bytes: 3,
    })
    render(form())

    await actor.click(screen.getByRole('button', { name: 'Проблема' }))
    await actor.type(screen.getByRole('textbox', { name: 'Заголовок *' }), 'Проблема')
    await actor.click(screen.getByRole('button', { name: 'Создать' }))
    await screen.findByText('Репорт отправлен оператору.')
    await actor.upload(screen.getByLabelText('Файл'), new File(['heic'], 'robot.HEIC', { type: '' }))
    await actor.click(screen.getByRole('button', { name: 'Прикрепить файл' }))

    await waitFor(() => expect(attach).toHaveBeenCalledWith(42, 'device_photo', expect.any(File)))
  })

  it.each([
    ['image/jpg', 'robot.jpg', 'image/jpg'],
    ['generic MIME without a recognized extension', 'camera-upload', 'application/octet-stream'],
    ['binary generic MIME without a recognized extension', 'camera-upload', 'binary/octet-stream'],
  ])('uploads a server-supported %s image without creating a second report', async (_label, filename, type) => {
    const actor = userEvent.setup()
    const create = vi.spyOn(api, 'createReport').mockResolvedValue(created)
    const attach = vi.spyOn(api, 'reportAttach').mockResolvedValue({
      id: 11, kind: 'device_photo', filename, content_type: 'image/jpeg', size_bytes: 3,
    })
    render(form())

    await actor.click(screen.getByRole('button', { name: 'Проблема' }))
    await actor.type(screen.getByRole('textbox', { name: 'Заголовок *' }), 'Проблема')
    await actor.click(screen.getByRole('button', { name: 'Создать' }))
    await screen.findByText('Репорт отправлен оператору.')
    await actor.upload(screen.getByLabelText('Файл'), new File(['jpeg bytes'], filename, { type }))
    await actor.click(screen.getByRole('button', { name: 'Прикрепить файл' }))

    await waitFor(() => expect(attach).toHaveBeenCalledWith(42, 'device_photo', expect.any(File)))
    expect(create).toHaveBeenCalledTimes(1)
  })

  it('restores a non-secret principal-and-park draft after remount and keeps it after a network error', async () => {
    const actor = userEvent.setup()
    const createReport = vi.fn().mockRejectedValue(new TypeError('offline'))
    const first = render(form({ apiClient: client({ createReport }) }))

    await actor.click(screen.getByRole('button', { name: 'Проблема' }))
    await actor.type(screen.getByRole('textbox', { name: 'Заголовок *' }), 'Не едет')
    await actor.type(screen.getByRole('textbox', { name: 'Описание' }), 'Колесо заблокировано')
    await actor.click(screen.getByRole('button', { name: 'Создать' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось выполнить действие')
    first.unmount()

    render(form({ apiClient: client({ createReport }) }))
    expect(screen.getByRole('button', { name: 'Проблема' })).toHaveClass('is-active')
    expect(screen.getByRole('textbox', { name: 'Заголовок *' })).toHaveValue('Не едет')
    expect(screen.getByRole('textbox', { name: 'Описание' })).toHaveValue('Колесо заблокировано')
    expect(localStorage.getItem('robopark:report-draft:1:7')).not.toContain('File')
  })

  it('clears the scoped draft after successful creation', async () => {
    const actor = userEvent.setup()
    const createReport = vi.fn().mockResolvedValue(created)
    render(form({ apiClient: client({ createReport }) }))

    await actor.click(screen.getByRole('button', { name: 'Проблема' }))
    await actor.type(screen.getByRole('textbox', { name: 'Заголовок *' }), 'Не едет')
    expect(localStorage.getItem('robopark:report-draft:1:7')).toContain('Не едет')
    await actor.click(screen.getByRole('button', { name: 'Создать' }))

    await screen.findByText('Репорт отправлен оператору.')
    expect(localStorage.getItem('robopark:report-draft:1:7')).toBeNull()
  })

  it('drops a late create result after owner change and never exposes its report to uploads', async () => {
    const actor = userEvent.setup()
    const pending = deferred<Report>()
    const createReport = vi.fn()
      .mockImplementationOnce(() => pending.promise)
      .mockResolvedValueOnce({ ...created, id: 84, park_id: 8 })
    const reportAttach = vi.fn().mockResolvedValue({
      id: 12, kind: 'device_photo', filename: 'next.jpg', content_type: 'image/jpeg', size_bytes: 4,
    })
    const onCreated = vi.fn()
    const view = render(form({ apiClient: client({ createReport, reportAttach }), onCreated }))

    await actor.click(screen.getByRole('button', { name: 'Проблема' }))
    await actor.type(screen.getByRole('textbox', { name: 'Заголовок *' }), 'Старый владелец')
    await actor.click(screen.getByRole('button', { name: 'Создать' }))
    view.rerender(form({
      apiClient: client({ createReport, reportAttach }),
      onCreated,
      ownerKey: 'owner-b',
      parkId: 8,
      principalId: 2,
    }))

    await act(async () => pending.resolve(created))
    expect(screen.queryByText('Репорт отправлен оператору.')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Вложения к репорту')).not.toBeInTheDocument()
    expect(onCreated).not.toHaveBeenCalled()

    await actor.click(screen.getByRole('button', { name: 'Проблема' }))
    await actor.type(screen.getByRole('textbox', { name: 'Заголовок *' }), 'Новый владелец')
    await actor.click(screen.getByRole('button', { name: 'Создать' }))
    await screen.findByLabelText('Вложения к репорту')
    await actor.upload(screen.getByLabelText('Файл'), new File(['next'], 'next.jpg', { type: 'image/jpeg' }))
    await actor.click(screen.getByRole('button', { name: 'Прикрепить файл' }))

    await waitFor(() => expect(reportAttach).toHaveBeenCalledWith(84, 'device_photo', expect.any(File)))
    expect(reportAttach).not.toHaveBeenCalledWith(42, expect.anything(), expect.anything())
  })

  it('ignores a late upload success after the owner changes', async () => {
    const actor = userEvent.setup()
    const pending = deferred<{ id: number; kind: 'device_photo'; filename: string; content_type: string; size_bytes: number }>()
    const reportAttach = vi.fn(() => pending.promise)
    const apiClient = client({ createReport: vi.fn().mockResolvedValue(created), reportAttach })
    const view = render(form({ apiClient }))

    await actor.click(screen.getByRole('button', { name: 'Проблема' }))
    await actor.type(screen.getByRole('textbox', { name: 'Заголовок *' }), 'Старый')
    await actor.click(screen.getByRole('button', { name: 'Создать' }))
    await screen.findByLabelText('Вложения к репорту')
    await actor.upload(screen.getByLabelText('Файл'), new File(['old'], 'old.jpg', { type: 'image/jpeg' }))
    await actor.click(screen.getByRole('button', { name: 'Прикрепить файл' }))

    view.rerender(form({ apiClient, ownerKey: 'owner-b', parkId: 8, principalId: 2 }))
    await act(async () => pending.resolve({ id: 1, kind: 'device_photo', filename: 'old.jpg', content_type: 'image/jpeg', size_bytes: 3 }))
    expect(screen.queryByText('Файл прикреплён к созданному репорту.')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Вложения к репорту')).not.toBeInTheDocument()
  })
})
