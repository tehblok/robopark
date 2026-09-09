import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { Panel } from './PageShell'

it('restores a panel collapse state from its explicit storage key', () => {
  localStorage.setItem('robopark:panel:reports-mine:collapsed', '1')

  render(
    <Panel collapsible storageKey="reports-mine" title="Мои репорты">
      <p>Список репортов</p>
    </Panel>,
  )

  expect(screen.queryByText('Список репортов')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Развернуть: Мои репорты' }))
  expect(screen.getByText('Список репортов')).toBeVisible()
  expect(localStorage.getItem('robopark:panel:reports-mine:collapsed')).toBeNull()
})

it('rejects a collapsible panel without a stable storage key in development', () => {
  expect(() => render(<Panel collapsible title="Мои репорты"><p>Список репортов</p></Panel>)).toThrow(
    'A collapsible Panel requires a nonempty title and storageKey.',
  )
})
