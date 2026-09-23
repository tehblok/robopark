import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { SubmitReviewForm } from './SubmitReviewForm'
import { ApiError } from '../../api'

const codes = [{ code: 'BD-01', label: 'Вмятина', description: null }]

beforeEach(() => {
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
  vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:preview'), revokeObjectURL: vi.fn() })
})
afterEach(() => vi.unstubAllGlobals())

it('blocks duplicate submissions and preserves the form after a rejected action', async () => {
  let reject!: (reason: unknown) => void
  const onSubmit = vi.fn(() => new Promise<void>((_, fail) => { reject = fail }))
  render(<SubmitReviewForm defectCodes={codes} hasQualifyingComment onSubmit={onSubmit} />)
  const code = screen.getByRole('combobox', { name: 'Код дефекта' })
  fireEvent.change(code, { target: { value: 'BD-01' } })
  fireEvent.change(screen.getByLabelText('Сделать фото или выбрать файл'), { target: { files: [new File(['image'], 'fixed.jpg', { type: 'image/jpeg' })] } })
  const form = code.closest('form')!
  fireEvent.submit(form)
  fireEvent.submit(form)
  expect(onSubmit).toHaveBeenCalledTimes(1)
  expect(code).toBeDisabled()
  reject(new ApiError(409, 'task_already_closed'))
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Задача уже закрыта'))
  expect(code).toHaveValue('BD-01')
  expect(screen.getByRole('img', { name: 'Предпросмотр fixed.jpg' })).toBeVisible()
  expect(code).not.toBeDisabled()
})

it('requires a completion note when the current cycle has no mechanic comment', async () => {
  const onSubmit = vi.fn(async () => undefined)
  render(<SubmitReviewForm defectCodes={codes} hasQualifyingComment={false} onSubmit={onSubmit} />)
  expect(screen.getByText('Напишите, что было сделано перед передачей на проверку')).toBeVisible()
  expect(screen.getByRole('textbox', { name: 'Комментарий о выполненной работе' })).toBeRequired()
})

it('uses one replaceable photo state and omits an empty optional comment', async () => {
  const onSubmit = vi.fn(async () => undefined)
  render(<SubmitReviewForm defectCodes={codes} hasQualifyingComment onSubmit={onSubmit} />)
  fireEvent.change(screen.getByRole('combobox', { name: 'Код дефекта' }), { target: { value: 'BD-01' } })
  const input = screen.getByLabelText('Сделать фото или выбрать файл') as HTMLInputElement
  const photo = new File(['image'], 'fixed.jpg', { type: 'image/jpeg' })
  fireEvent.change(input, { target: { files: [photo] } })
  expect(screen.getByRole('img', { name: 'Предпросмотр fixed.jpg' })).toBeVisible()
  expect(screen.getByRole('textbox', { name: 'Добавить уточнение' })).not.toBeRequired()
  fireEvent.click(screen.getByRole('button', { name: 'Передать на проверку' }))
  await waitFor(() => expect(onSubmit).toHaveBeenCalledWith({ defectCode: 'BD-01', photo, comment: undefined }))
})

it('uses the mobile form stack and Button file actions with stable labels', () => {
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
  render(<SubmitReviewForm defectCodes={codes} hasQualifyingComment onSubmit={vi.fn()} />)
  const fieldset = screen.getByRole('group')
  expect(fieldset).toHaveClass('rp-form-stack--mobile')
  expect(fieldset).not.toHaveAttribute('style')

  const fileInput = screen.getByLabelText('Сделать фото или выбрать файл')
  expect(fileInput).toHaveAttribute('accept', 'image/jpeg,image/png,image/webp')
  expect(screen.getByRole('button', { name: 'Сделать фото или выбрать файл' })).toHaveClass('rp-button--secondary')
  expect(screen.getByRole('button', { name: 'Сделать фото или выбрать файл' }).parentElement).toHaveClass('rp-action-bar')

  fireEvent.change(fileInput, { target: { files: [new File(['image'], 'fixed.jpg', { type: 'image/jpeg' })] } })
  expect(screen.getByRole('button', { name: 'Заменить' })).toHaveClass('rp-button--secondary')
  expect(screen.getByRole('button', { name: 'Удалить' })).toHaveClass('rp-button--ghost')
})
