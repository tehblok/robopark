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
  const first = new File(['before'], 'before.jpg', { type: 'image/jpeg' })
  const photo = new File(['after'], 'fixed.jpg', { type: 'image/jpeg' })
  fireEvent.change(input, { target: { files: [first] } })
  fireEvent.change(input, { target: { files: [photo] } })
  expect(screen.queryByRole('img', { name: 'Предпросмотр before.jpg' })).not.toBeInTheDocument()
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
  expect(fileInput).toHaveAttribute('capture', 'environment')
  expect(screen.getByRole('button', { name: 'Сделать фото или выбрать файл' })).toHaveClass('rp-button--secondary')
  expect(screen.getByRole('button', { name: 'Сделать фото или выбрать файл' }).parentElement).toHaveClass('rp-action-bar')

  fireEvent.change(fileInput, { target: { files: [new File(['image'], 'fixed.jpg', { type: 'image/jpeg' })] } })
  expect(screen.getByRole('button', { name: 'Заменить' })).toHaveClass('rp-button--secondary')
  expect(screen.getByRole('button', { name: 'Удалить' })).toHaveClass('rp-button--ghost')
})

const repairOptions = {
  issue_key: 'RP-77', components: [{ id: 'wheel', label: 'Мотор-колесо' }, { id: 'camera', label: 'Камера' }],
  selected_component_ids: ['wheel'], suggested_component_ids: [], suggestion_reason: null,
  defect_code: 'BD-01', solution_method: null,
  solution_methods: [{ code: 'CHANGE', label: 'Заменил' }, { code: 'REPAIR', label: 'Отремонтировал' }],
  field_snapshot: { component_ids: ['wheel'], defect_code: 'BD-01', solution_method: null },
}

it('links component and defect suggestions without inventing or resetting the performed action', () => {
  const linkedCodes = [...codes, { code: 'EL-06', label: 'Не откалибровано', description: null }, { code: 'EL-10', label: 'Нет изображения с камеры', description: null }]
  const options = { ...repairOptions, defect_code: null, selected_component_ids: ['camera'], components: [
    { id: 'camera', label: 'Камера', defect_codes: ['EL-06', 'EL-10'], solution_methods: ['CHANGE', 'CONFIG'] },
    { id: 'wheel', label: 'Колесо', defect_codes: ['BD-01'], solution_methods: ['REPAIR'] },
  ], solution_methods: [...repairOptions.solution_methods, { code: 'CONFIG', label: 'Настроил' }, { code: 'DIAG', label: 'Провёл диагностику' }], defect_method_suggestions: { 'EL-06': ['CONFIG', 'DIAG'] } }
  render(<SubmitReviewForm defectCodes={linkedCodes} hasQualifyingComment repairOptions={options} onSubmit={vi.fn()} />)
  const related = screen.getByRole('group', { name: 'Для выбранной детали' })
  expect(related).toHaveTextContent('Не откалибровано')
  expect(related).not.toHaveTextContent('Вмятина')
  fireEvent.change(screen.getByRole('combobox', { name: 'Что случилось?' }), { target: { value: 'EL-06' } })
  expect(screen.getByRole('group', { name: 'Что сделали?' }).querySelector('button')).toHaveTextContent('Настроил')
  expect(screen.getByRole('button', { name: 'Настроил' })).toHaveAttribute('aria-pressed', 'false')
  fireEvent.click(screen.getByRole('button', { name: 'Настроил' }))
  fireEvent.click(screen.getByRole('checkbox', { name: 'Колесо' }))
  expect(screen.getByRole('combobox', { name: 'Что случилось?' })).toHaveValue('EL-06')
  expect(screen.getByRole('button', { name: 'Настроил' })).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByRole('option', { name: 'Вмятина · BD-01' })).toBeInTheDocument()
})

it('submits explicit repair choices without making the mechanic retype the structured report', async () => {
  const onSubmit = vi.fn(async (_value: import('./SubmitReviewForm').SubmitReviewValue) => undefined)
  render(<SubmitReviewForm defectCodes={codes} hasQualifyingComment={false} repairOptions={repairOptions} onSubmit={onSubmit} />)
  expect(screen.getByRole('combobox', { name: 'Что случилось?' })).toHaveValue('BD-01')
  expect(screen.getByRole('button', { name: 'Передать на проверку' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Заменил' }))
  const photo = new File(['image'], 'fixed.jpg', { type: 'image/jpeg' })
  fireEvent.change(screen.getByLabelText('Сделать фото или выбрать файл'), { target: { files: [photo] } })
  expect(screen.getByRole('textbox', { name: 'Добавить уточнение' })).not.toBeRequired()
  fireEvent.click(screen.getByRole('button', { name: 'Передать на проверку' }))
  await waitFor(() => expect(onSubmit).toHaveBeenCalledWith({
    defectCode: 'BD-01', photo, comment: undefined,
    repairFields: { componentIds: ['wheel'], solutionMethod: 'CHANGE', expected: repairOptions.field_snapshot },
  }))
})

it('keeps chosen fields and their original snapshot on a catalog refresh and failed submission', async () => {
  const onSubmit = vi.fn(async (_value: import('./SubmitReviewForm').SubmitReviewValue) => { throw new ApiError(409, 'repair_fields_conflict') })
  const view = render(<SubmitReviewForm defectCodes={codes} hasQualifyingComment={false} repairOptions={repairOptions} onSubmit={onSubmit} />)
  fireEvent.click(screen.getByRole('button', { name: 'Отремонтировал' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Добавить уточнение' }), { target: { value: 'Проверил под нагрузкой' } })
  fireEvent.change(screen.getByLabelText('Сделать фото или выбрать файл'), { target: { files: [new File(['photo'], 'result.jpg', { type: 'image/jpeg' })] } })
  view.rerender(<SubmitReviewForm defectCodes={codes} hasQualifyingComment={false} repairOptions={{ ...repairOptions, solution_method: 'CHANGE', field_snapshot: { ...repairOptions.field_snapshot, solution_method: 'CHANGE' } }} onSubmit={onSubmit} />)
  expect(screen.getByRole('button', { name: 'Отремонтировал' })).toHaveAttribute('aria-pressed', 'true')
  fireEvent.click(screen.getByRole('button', { name: 'Передать на проверку' }))
  await screen.findByRole('alert')
  expect(screen.getByRole('textbox', { name: 'Добавить уточнение' })).toHaveValue('Проверил под нагрузкой')
  expect(screen.getByRole('img', { name: 'Предпросмотр result.jpg' })).toBeVisible()
  expect(onSubmit.mock.calls[0][0].repairFields?.expected).toEqual(repairOptions.field_snapshot)
})

it('reloads conflicting fields explicitly without losing photo or clarification', async () => {
  const onSubmit = vi.fn(async () => { throw new ApiError(409, 'repair_fields_conflict') })
  const latest = { ...repairOptions, selected_component_ids: ['camera'], solution_method: 'REPAIR', field_snapshot: { ...repairOptions.field_snapshot, component_ids: ['camera'], solution_method: 'REPAIR' } }
  const onRefreshOptions = vi.fn(async () => latest)
  render(<SubmitReviewForm defectCodes={codes} hasQualifyingComment={false} repairOptions={repairOptions} onSubmit={onSubmit} onRefreshOptions={onRefreshOptions} />)
  fireEvent.click(screen.getByRole('button', { name: 'Заменил' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Добавить уточнение' }), { target: { value: 'Проверено' } })
  fireEvent.change(screen.getByLabelText('Сделать фото или выбрать файл'), { target: { files: [new File(['photo'], 'result.jpg', { type: 'image/jpeg' })] } })
  fireEvent.click(screen.getByRole('button', { name: 'Передать на проверку' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Загрузить актуальные поля' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Отремонтировал' })).toHaveAttribute('aria-pressed', 'true'))
  expect(screen.getByText(/Что ремонтируем: Камера/)).toBeVisible()
  expect(screen.getByRole('textbox', { name: 'Добавить уточнение' })).toHaveValue('Проверено')
  expect(screen.getByRole('img', { name: 'Предпросмотр result.jpg' })).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Передать на проверку' }))
  await waitFor(() => expect(onSubmit).toHaveBeenLastCalledWith(expect.objectContaining({ repairFields: expect.objectContaining({ expected: latest.field_snapshot }) })))
})
