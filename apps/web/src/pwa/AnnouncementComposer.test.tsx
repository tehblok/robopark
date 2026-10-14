import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { AnnouncementComposer } from './AnnouncementComposer'
import type { AnnouncementRequest } from '../api'

const receipt = (value: AnnouncementRequest) => ({ ...value, event_id: 'event1', action: null, counters: { inbox_created: 12, push_eligible: 9 } })

it('sends a maintenance announcement only after an explicit form submit', async () => {
  const send = vi.fn(async (value: AnnouncementRequest) => receipt(value))
  render(<AnnouncementComposer send={send} />)
  fireEvent.change(screen.getByLabelText('Тип сообщения'), { target: { value: 'maintenance' } })
  fireEvent.change(screen.getByLabelText('Текст сообщения'), { target: { value: '  Техработы в 23:00. Сохраните изменения.  ' } })
  expect(send).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Отправить сотрудникам' }))
  expect(await screen.findByRole('status')).toHaveTextContent('Новых сообщений: 12')
  expect(send).toHaveBeenCalledWith({ request_id: expect.any(String), event_type: 'maintenance', protected_text: 'Техработы в 23:00. Сохраните изменения.' })
})

it('reuses the request id when a lost response is retried', async () => {
  const send = vi.fn<(value: AnnouncementRequest) => Promise<ReturnType<typeof receipt>>>()
    .mockRejectedValueOnce(new Error('lost response')).mockImplementation(async value => receipt(value))
  render(<AnnouncementComposer send={send} />)
  fireEvent.change(screen.getByLabelText('Текст сообщения'), { target: { value: 'Проверьте рабочие задачи' } })
  fireEvent.click(screen.getByRole('button', { name: 'Отправить сотрудникам' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Повторите')
  fireEvent.click(screen.getByRole('button', { name: 'Отправить сотрудникам' }))
  await waitFor(() => expect(send).toHaveBeenCalledTimes(2))
  expect(send.mock.calls[1][0]).toEqual(send.mock.calls[0][0])
})
