import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { SubmitReviewForm } from './SubmitReviewForm'

const codes = [{ code: 'BD-01', label: 'Вмятина', description: null }]

beforeEach(() => {
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
  vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:preview'), revokeObjectURL: vi.fn() })
})
afterEach(() => vi.unstubAllGlobals())

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
  const input = screen.getByLabelText('Выбрать файл') as HTMLInputElement
  const photo = new File(['image'], 'fixed.jpg', { type: 'image/jpeg' })
  fireEvent.change(input, { target: { files: [photo] } })
  expect(screen.getByRole('img', { name: 'Предпросмотр fixed.jpg' })).toBeVisible()
  expect(screen.getByRole('textbox', { name: 'Добавить уточнение' })).not.toBeRequired()
  fireEvent.click(screen.getByRole('button', { name: 'Передать на проверку' }))
  await waitFor(() => expect(onSubmit).toHaveBeenCalledWith({ defectCode: 'BD-01', photo, comment: undefined }))
})

it('offers separate phone camera and file controls without capturing on render', () => {
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
  render(<SubmitReviewForm defectCodes={codes} hasQualifyingComment onSubmit={vi.fn()} />)
  const camera = screen.getByLabelText('Сделать фото')
  const file = screen.getByLabelText('Выбрать файл')
  expect(camera).toHaveAttribute('accept', 'image/*')
  expect(camera).toHaveAttribute('capture', 'environment')
  expect(file).toHaveAttribute('accept', 'image/jpeg,image/png,image/webp')
  expect(file).not.toHaveAttribute('capture')
})
