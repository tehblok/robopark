import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { expect, it } from 'vitest'
import { DomainPresentation } from './DomainPresentation'

function Draft() {
  const [open, setOpen] = useState(false)
  return <>
    <label>Название роли<input aria-label="Название роли" /></label>
    <button type="button" onClick={() => setOpen(true)}>Открыть диалог</button>
    {open ? <div role="dialog">Несохранённый пользователь</div> : null}
  </>
}

it('keeps the owned subtree mounted across classic rerenders', () => {
  const view = render(<DomainPresentation route="admin-roles" context={<p>Контекст</p>}><Draft /></DomainPresentation>)
  const input = screen.getByRole('textbox', { name: 'Название роли' })
  fireEvent.change(input, { target: { value: 'Несохранённая роль' } })
  fireEvent.click(screen.getByRole('button', { name: 'Открыть диалог' }))

  view.rerender(<DomainPresentation route="admin-roles" context={<p>Контекст</p>}><Draft /></DomainPresentation>)

  expect(screen.getByRole('textbox', { name: 'Название роли' })).toBe(input)
  expect(input).toHaveValue('Несохранённая роль')
  expect(screen.getByRole('dialog')).toHaveTextContent('Несохранённый пользователь')
  expect(screen.getByText('Контекст').closest('aside')).not.toBeNull()
})
