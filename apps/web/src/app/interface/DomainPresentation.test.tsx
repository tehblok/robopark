import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { expect, it } from 'vitest'
import { DomainPresentation } from './DomainPresentation'
import { PresentationModeContext } from './presentationModeContext'

function Draft() {
  const [open, setOpen] = useState(false)
  return <>
    <label>Название роли<input aria-label="Название роли" /></label>
    <button type="button" onClick={() => setOpen(true)}>Открыть диалог</button>
    {open ? <div role="dialog">Несохранённый пользователь</div> : null}
  </>
}

it('keeps the owned subtree mounted while presentation attributes change', () => {
  const view = render(
    <PresentationModeContext.Provider value="classic">
      <DomainPresentation route="admin-roles" context={<p>Контекст</p>}><Draft /></DomainPresentation>
    </PresentationModeContext.Provider>,
  )
  const input = screen.getByRole('textbox', { name: 'Название роли' })
  fireEvent.change(input, { target: { value: 'Несохранённая роль' } })
  fireEvent.click(screen.getByRole('button', { name: 'Открыть диалог' }))

  view.rerender(
    <PresentationModeContext.Provider value="task-first">
      <DomainPresentation route="admin-roles" context={<p>Контекст</p>}><Draft /></DomainPresentation>
    </PresentationModeContext.Provider>,
  )

  expect(screen.getByRole('textbox', { name: 'Название роли' })).toBe(input)
  expect(input).toHaveValue('Несохранённая роль')
  expect(screen.getByRole('dialog')).toHaveTextContent('Несохранённый пользователь')
  expect(input.closest('[data-a-zone="workflow"]')).not.toBeNull()
})
