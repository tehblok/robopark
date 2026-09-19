import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { ReturnReviewForm } from './ReturnReviewForm'

it('requires a reason and retains it after failure for retry', async () => {
  const submit = vi.fn().mockRejectedValueOnce(new Error('network')).mockResolvedValueOnce(undefined)
  const cancel = vi.fn()
  render(<ReturnReviewForm onSubmit={submit} onCancel={cancel} />)
  expect(screen.getByRole('button', { name: 'Вернуть задачу' })).toBeDisabled()
  fireEvent.change(screen.getByRole('textbox', { name: 'Что нужно исправить' }), { target: { value: ' Проверить колесо ' } })
  fireEvent.click(screen.getByRole('button', { name: 'Вернуть задачу' }))
  await screen.findByRole('alert')
  expect(submit).toHaveBeenCalledWith('Проверить колесо')
  expect(screen.getByRole('textbox')).toHaveValue(' Проверить колесо ')
  fireEvent.click(screen.getByRole('button', { name: 'Вернуть задачу' }))
  await waitFor(() => expect(submit).toHaveBeenCalledTimes(2))
  fireEvent.click(screen.getByRole('button', { name: 'Отмена' }))
  expect(cancel).toHaveBeenCalledOnce()
})
