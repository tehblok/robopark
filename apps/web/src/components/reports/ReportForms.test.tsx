import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import { Blob as NativeBlob, File as NativeFile } from 'node:buffer'
import * as drafts from '../../domains/reports/reportPhotoDrafts'
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
  beforeEach(async () => {
    vi.stubGlobal('indexedDB', new IDBFactory())
    vi.stubGlobal('File', NativeFile)
    vi.stubGlobal('Blob', NativeBlob)
    await drafts.clearReportPhotoDrafts()
  })
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

    await waitFor(() => expect(screen.getByRole('button', { name: 'Проблема' })).toBeEnabled())
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

    await waitFor(() => expect(screen.getByRole('button', { name: 'Проблема' })).toBeEnabled())
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

    await waitFor(() => expect(screen.getByRole('button', { name: 'Проблема' })).toBeEnabled())
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

    await waitFor(() => expect(screen.getByRole('button', { name: 'Проблема' })).toBeEnabled())
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

    await waitFor(() => expect(screen.getByRole('button', { name: 'Проблема' })).toBeEnabled())
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

    await waitFor(() => expect(screen.getByRole('button', { name: 'Проблема' })).toBeEnabled())
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

    await waitFor(() => expect(screen.getByRole('button', { name: 'Проблема' })).toBeEnabled())
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

    await waitFor(() => expect(screen.getByRole('button', { name: 'Проблема' })).toBeEnabled())
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
    expect(screen.getByLabelText('Файл')).toHaveValue('')
  })
  it('restores a selected photo and failed attachment after reload without creating a duplicate report', async () => {
    const actor = userEvent.setup()
    const createReport = vi.fn().mockResolvedValue(created)
    const reportAttach = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ id: 11 })
    const apiClient = client({ createReport, reportAttach })
    let view = render(form({ apiClient }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Проблема' })).toBeEnabled())
    await actor.click(screen.getByRole('button', { name: 'Проблема' }))
    await actor.type(screen.getByRole('textbox', { name: 'Заголовок *' }), 'С фото')
    await actor.upload(screen.getByLabelText('Файл'), new File(['photo bytes'], 'robot.jpg', { type: 'image/jpeg' }))
    await waitFor(() => expect(screen.getByText('Черновик сохранён на этом устройстве.')).toBeVisible())
    view.unmount()
    view = render(form({ apiClient }))
    await screen.findByText(/robot.jpg/)
    await actor.click(screen.getByRole('button', { name: 'Создать' }))
    await screen.findByText('Репорт отправлен оператору.')
    await actor.click(screen.getByRole('button', { name: 'Прикрепить файл' }))
    await screen.findByRole('alert')
    await waitFor(async () => expect((await drafts.readReportPhotoDraft('robopark:report-draft:1:7'))?.createdReportId).toBe(42))
    view.unmount()
    render(form({ apiClient }))
    await screen.findByText(/robot.jpg/)
    expect(screen.getByRole('button', { name: 'Создать' })).toBeDisabled()
    await actor.click(screen.getByRole('button', { name: 'Прикрепить файл' }))
    await screen.findByText('Файл прикреплён к созданному репорту.')
    expect(createReport).toHaveBeenCalledTimes(1)
    expect(reportAttach).toHaveBeenLastCalledWith(42, 'device_photo', expect.any(File))
    expect(await reportAttach.mock.calls[1][2].text()).toBe('photo bytes')
    await waitFor(async () => expect(await drafts.readReportPhotoDraft('robopark:report-draft:1:7')).toBeNull())
  })

  it('shows storage failure while keeping the selected file usable and deletable', async () => {
    const actor = userEvent.setup()
    vi.spyOn(drafts, 'writeReportPhotoDraft').mockRejectedValue(new DOMException('full', 'QuotaExceededError'))
    render(form())
    await waitFor(() => expect(screen.getByRole('button', { name: 'Проблема' })).toBeEnabled())
    await actor.click(screen.getByRole('button', { name: 'Проблема' }))
    await actor.type(screen.getByRole('textbox', { name: 'Заголовок *' }), 'Не терять')
    await actor.upload(screen.getByLabelText('Файл'), new File(['photo'], 'keep.jpg', { type: 'image/jpeg' }))
    expect(await screen.findByText(/Не удалось сохранить черновик/)).toBeVisible()
    expect(screen.getByRole('textbox', { name: 'Заголовок *' })).toHaveValue('Не терять')
    expect(screen.getByText(/keep.jpg/)).toBeVisible()
    await actor.click(screen.getByRole('button', { name: 'Удалить черновик' }))
    expect(screen.getByRole('textbox', { name: 'Заголовок *' })).toHaveValue('')
    expect(screen.queryByText(/keep.jpg/)).not.toBeInTheDocument()
  })

  it('does not expose a late IndexedDB result to a different account', async () => {
    const pending = deferred<drafts.ReportPhotoDraft | null>()
    vi.spyOn(drafts, 'readReportPhotoDraft').mockImplementationOnce(() => pending.promise).mockResolvedValue(null)
    const view = render(form())
    view.rerender(form({ ownerKey: 'owner-b', principalId: 2, parkId: 8 }))
    await act(async () => pending.resolve({ key: 'robopark:report-draft:1:7', ownerKey: 'owner-a', revision: 'old', activeForm: 'problem', title: 'Секретный старый', body: '', trackerKey: '', createdReportId: 42, attachmentKind: 'device_photo', attachment: { blob: new Blob(['old']), name: 'old.jpg', lastModified: 0 } }))
    expect(screen.getByRole('textbox', { name: 'Заголовок *' })).toHaveValue('')
    expect(screen.queryByText(/old.jpg/)).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Вложения к репорту')).not.toBeInTheDocument()
  })


  it.each(['owner', 'discard'] as const)('drops a late restored photo after %s changes while its bytes load', async (change) => {
    const bytes = deferred<ArrayBuffer>()
    const blob = new Blob(['saved photo'], { type: 'image/jpeg' })
    const originalBytes = await blob.arrayBuffer()
    const readBytes = vi.spyOn(blob, 'arrayBuffer').mockReturnValue(bytes.promise)
    vi.spyOn(drafts, 'readReportPhotoDraft').mockResolvedValueOnce({
      key: 'robopark:report-draft:1:7', ownerKey: 'owner-a', revision: 'saved',
      activeForm: 'problem', title: 'Saved title', body: '', trackerKey: '', createdReportId: 42,
      attachmentKind: 'device_photo', attachment: { blob, name: 'saved.jpg', lastModified: 0 },
    }).mockResolvedValue(null)
    const view = render(form())
    await waitFor(() => expect(readBytes).toHaveBeenCalledOnce())
    if (change === 'owner') view.rerender(form({ ownerKey: 'owner-b', principalId: 2, parkId: 8 }))
    else await userEvent.setup().click(screen.getByRole('button', { name: 'Удалить черновик' }))
    await act(async () => bytes.resolve(originalBytes))
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Заголовок *' })).toBeEnabled())
    expect(screen.getByRole('textbox', { name: 'Заголовок *' })).toHaveValue('')
    expect(screen.queryByText(/saved.jpg/)).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Вложения к репорту')).not.toBeInTheDocument()
  })


  it('preserves an acknowledged IDB-only report when its photo bytes cannot be read', async () => {
    const blob = new Blob(['saved photo'], { type: 'image/jpeg' })
    vi.spyOn(blob, 'arrayBuffer').mockRejectedValue(new Error('unreadable photo'))
    const original: drafts.ReportPhotoDraft = {
      key: 'robopark:report-draft:1:7', ownerKey: 'owner-a', revision: 'saved',
      activeForm: 'problem', title: 'Saved title', body: 'Saved body', trackerKey: '', createdReportId: 42,
      attachmentKind: 'device_photo', attachment: { blob, name: 'saved.jpg', lastModified: 0 },
    }
    vi.spyOn(drafts, 'readReportPhotoDraft').mockResolvedValue(original)
    const write = vi.spyOn(drafts, 'writeReportPhotoDraft')
    render(form())
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Заголовок *' })).toBeEnabled())
    expect(screen.getByRole('textbox', { name: 'Заголовок *' })).toHaveValue('Saved title')
    expect(screen.getByRole('textbox', { name: 'Описание' })).toHaveValue('Saved body')
    expect(screen.getByText(/Репорт №42/)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Создать' })).toBeDisabled()
    expect(screen.queryByText(/Выбран файл: saved.jpg/)).not.toBeInTheDocument()
    expect(screen.getByRole('alert')).toBeVisible()
    expect(write).not.toHaveBeenCalled()
    expect(JSON.parse(localStorage.getItem('robopark:report-draft:1:7')!)).toMatchObject({ createdReportId: 42 })
    await userEvent.setup().upload(screen.getByLabelText('Файл'), new File(['replacement'], 'replacement.jpg', { type: 'image/jpeg' }))
    await waitFor(() => expect(write).toHaveBeenCalledWith(expect.objectContaining({
      createdReportId: 42, attachment: expect.objectContaining({ name: 'replacement.jpg' }),
    })))
  })

  it('keeps attachment type locked while restoring photo bytes', async () => {
    const bytes = deferred<ArrayBuffer>()
    const blob = new Blob(['saved photo'], { type: 'image/jpeg' })
    const originalBytes = await blob.arrayBuffer()
    const readBytes = vi.spyOn(blob, 'arrayBuffer').mockReturnValue(bytes.promise)
    vi.spyOn(drafts, 'readReportPhotoDraft').mockResolvedValue({
      key: 'robopark:report-draft:1:7', ownerKey: 'owner-a', revision: 'saved',
      activeForm: 'problem', title: 'Saved title', body: '', trackerKey: '', createdReportId: 42,
      attachmentKind: 'ui_snapshot', attachment: { blob, name: 'saved.jpg', lastModified: 0 },
    })
    render(form())
    await waitFor(() => expect(readBytes).toHaveBeenCalledOnce())
    expect(screen.getByRole('combobox', { name: 'Тип вложения' })).toBeDisabled()
    await act(async () => bytes.resolve(originalBytes))
    expect(screen.getByRole('combobox', { name: 'Тип вложения' })).toBeEnabled()
    expect(screen.getByRole('combobox', { name: 'Тип вложения' })).toHaveValue('ui_snapshot')
    expect(screen.getByText(/Выбран файл: saved.jpg/)).toBeVisible()
  })

  it('does not show an unscoped legacy text draft to a newly authorized scope', () => {
    localStorage.setItem('robopark:report-draft:1:7', JSON.stringify({ title: 'Legacy secret', body: 'private' }))
    render(form({ ownerKey: 'new-scope' }))
    expect(screen.getByRole('textbox', { name: 'Заголовок *' })).toHaveValue('')
  })

  it('preserves the acknowledged report ID and saved photo when the post-create disk write fails', async () => {
    const actor = userEvent.setup()
    const apiClient = client({ createReport: vi.fn().mockResolvedValue(created) })
    const view = render(form({ apiClient }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Проблема' })).toBeEnabled())
    await actor.click(screen.getByRole('button', { name: 'Проблема' }))
    await actor.type(screen.getByRole('textbox', { name: 'Заголовок *' }), 'Диск заполнен')
    await actor.upload(screen.getByLabelText('Файл'), new File(['photo'], 'saved.jpg', { type: 'image/jpeg' }))
    await screen.findByText('Черновик сохранён на этом устройстве.')
    const write = vi.spyOn(drafts, 'writeReportPhotoDraft').mockRejectedValue(new DOMException('full', 'QuotaExceededError'))
    await actor.click(screen.getByRole('button', { name: 'Создать' }))
    await screen.findByText(/Не удалось сохранить черновик/)
    view.unmount()
    write.mockRestore()
    render(form({ apiClient }))
    await screen.findByText(/saved.jpg/)
    expect(screen.getByRole('button', { name: 'Создать' })).toBeDisabled()
    expect(screen.getByText(/Репорт №42/)).toBeVisible()
    expect(screen.getByRole('textbox', { name: 'Заголовок *' })).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Прикрепить файл' })).toBeEnabled()
  })

  it('keeps newer text when the submitted revision succeeds', async () => {
    const actor = userEvent.setup()
    const pending = deferred<Report>()
    render(form({ apiClient: client({ createReport: vi.fn(() => pending.promise) }) }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Проблема' })).toBeEnabled())
    await actor.click(screen.getByRole('button', { name: 'Проблема' }))
    await actor.type(screen.getByRole('textbox', { name: 'Заголовок *' }), 'Отправленное')
    await actor.click(screen.getByRole('button', { name: 'Создать' }))
    await actor.clear(screen.getByRole('textbox', { name: 'Заголовок *' }))
    await actor.type(screen.getByRole('textbox', { name: 'Заголовок *' }), 'Новая правка')
    await act(async () => pending.resolve(created))
    expect(screen.getByRole('textbox', { name: 'Заголовок *' })).toHaveValue('Новая правка')
    expect(localStorage.getItem('robopark:report-draft:1:7')).toContain('Новая правка')
  })

  it('clears submitted text before the creation callback closes the form', async () => {
    const actor = userEvent.setup()
    const view = render(form({ apiClient: client({ createReport: vi.fn().mockResolvedValue(created) }), onCreated: () => view.unmount() }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Проблема' })).toBeEnabled())
    await actor.click(screen.getByRole('button', { name: 'Проблема' }))
    await actor.type(screen.getByRole('textbox', { name: 'Заголовок *' }), 'Отправлено')
    await actor.click(screen.getByRole('button', { name: 'Создать' }))
    await waitFor(() => expect(localStorage.getItem('robopark:report-draft:1:7')).toBeNull())
  })

  it('does not resurrect a delivered photo when deletion from IndexedDB fails', async () => {
    const actor = userEvent.setup()
    const apiClient = client({ createReport: vi.fn().mockResolvedValue(created), reportAttach: vi.fn().mockResolvedValue({ id: 11 }) })
    const view = render(form({ apiClient }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Проблема' })).toBeEnabled())
    await actor.click(screen.getByRole('button', { name: 'Проблема' }))
    await actor.type(screen.getByRole('textbox', { name: 'Заголовок *' }), 'Фото')
    await actor.upload(screen.getByLabelText('Файл'), new File(['photo'], 'delivered.jpg', { type: 'image/jpeg' }))
    await screen.findByText('Черновик сохранён на этом устройстве.')
    await actor.click(screen.getByRole('button', { name: 'Создать' }))
    await screen.findByText('Репорт отправлен оператору.')
    await waitFor(async () => expect((await drafts.readReportPhotoDraft('robopark:report-draft:1:7'))?.createdReportId).toBe(42))
    vi.spyOn(drafts, 'deleteReportPhotoDraft').mockRejectedValue(new Error('disk unavailable'))
    await actor.click(screen.getByRole('button', { name: 'Прикрепить файл' }))
    await screen.findByText('Файл прикреплён к созданному репорту.')
    await screen.findByText(/Не удалось сохранить черновик/)
    view.unmount()
    render(form({ apiClient }))
    await waitFor(() => expect(screen.getByLabelText('Файл')).toBeEnabled())
    expect(screen.queryByText(/Выбран файл: delivered.jpg/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Прикрепить файл' })).toBeDisabled()
  })

})
