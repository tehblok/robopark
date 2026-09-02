import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { FormField } from './FormField'

describe('FormField', () => {
  it('wires label, required state, hint, and error to the child control', () => {
    render(
      <FormField
        error="Обязательное поле"
        hint="До 100 знаков"
        id="title"
        label="Название"
        required
      >
        <input />
      </FormField>,
    )
    const field = screen.getByLabelText('Название')

    expect(field).toHaveAttribute('id', 'title')
    expect(field).toBeRequired()
    expect(field).toHaveAttribute('aria-invalid', 'true')
    expect(field).toHaveAttribute('aria-describedby', 'title-hint title-error')
    expect(screen.getByRole('alert')).toHaveAttribute('id', 'title-error')
  })

  it('preserves caller described-by tokens before stable hint and error tokens', () => {
    render(
      <FormField error="Ошибка" hint="Подсказка" id="serial" label="Серийный номер">
        <input aria-describedby="shared-details custom-details" />
      </FormField>,
    )

    expect(screen.getByLabelText('Серийный номер')).toHaveAttribute(
      'aria-describedby',
      'shared-details custom-details serial-hint serial-error',
    )
  })

  it('omits optional description metadata when no hint or error exists', () => {
    render(<FormField id="park" label="Парк"><select /></FormField>)

    const field = screen.getByLabelText('Парк')
    expect(field).toHaveAttribute('aria-invalid', 'false')
    expect(field).not.toHaveAttribute('aria-describedby')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
