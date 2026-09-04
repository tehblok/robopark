import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type Report } from '../../api'
import { ReportForms } from './ReportForms'

const created: Report = {
  id: 42, kind: 'mechanic_problem', status: 'open', park_id: 7, author_user_id: 1,
  target_role: 'operator', tracker_key: null, tracker_url: null, title: 'Проблема', body: '',
  parent_report_id: null, return_comment: null, created_at: '2026-09-04T08:00:00Z',
  updated_at: '2026-09-04T08:00:00Z', resolved_at: null,
}

describe('ReportForms', () => {
  afterEach(() => vi.restoreAllMocks())

  it('retries an attachment on the already-created report without posting the report again', async () => {
    const actor = userEvent.setup()
    const create = vi.spyOn(api, 'createReport').mockResolvedValue(created)
    const attach = vi.spyOn(api, 'reportAttach').mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({ id: 11, kind: 'device_photo', filename: 'robot.jpg', content_type: 'image/jpeg', size_bytes: 3 })
    render(<ReportForms onCreated={vi.fn()} parkId={7} />)

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
    render(<ReportForms onCreated={vi.fn()} parkId={7} />)

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
    render(<ReportForms onCreated={vi.fn()} parkId={7} />)

    await actor.click(screen.getByRole('button', { name: 'Проблема' }))
    await actor.type(screen.getByRole('textbox', { name: 'Заголовок *' }), 'Проблема')
    await actor.click(screen.getByRole('button', { name: 'Создать' }))
    await screen.findByText('Репорт отправлен оператору.')
    await actor.upload(screen.getByLabelText('Файл'), new File(['jpeg bytes'], filename, { type }))
    await actor.click(screen.getByRole('button', { name: 'Прикрепить файл' }))

    await waitFor(() => expect(attach).toHaveBeenCalledWith(42, 'device_photo', expect.any(File)))
    expect(create).toHaveBeenCalledTimes(1)
  })
})
