import { act, fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { SlaPolicyEditor } from './SlaPolicyEditor'
import { deferred, makeUser } from './operations.test-support'

it('requires parks.manage even for royal and does not read or save without it', () => {
  const client = { operationsSlaPolicy: vi.fn(), updateOperationsSlaPolicy: vi.fn() }
  render(<SlaPolicyEditor parkId={7} user={makeUser({ role: 'royal' })} apiClient={client} />)
  expect(screen.queryByRole('button')).not.toBeInTheDocument()
  expect(client.operationsSlaPolicy).not.toHaveBeenCalled()
})

it('loads existing target, rejects invalid input and explicitly clears without speculative saving', async () => {
  const pending = deferred<{ park_id: number; target_hours: number | null }>()
  const client = { operationsSlaPolicy: vi.fn(async () => ({ park_id: 7, target_hours: 24 })), updateOperationsSlaPolicy: vi.fn(() => pending.promise) }
  const changed = vi.fn()
  render(<SlaPolicyEditor parkId={7} user={makeUser({ permissions: ['parks.manage'] })} apiClient={client} onSaved={changed} />)
  const input = await screen.findByLabelText('Норматив, часов')
  expect(input).toHaveValue('24')
  expect(client.updateOperationsSlaPolicy).not.toHaveBeenCalled()
  for (const value of ['0', '8761', '1.5', '-4', 'abc']) {
    fireEvent.change(input, { target: { value } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить норматив' }))
    expect(screen.getByRole('alert')).toHaveTextContent('целое число от 1 до 8760')
  }
  expect(client.updateOperationsSlaPolicy).not.toHaveBeenCalled()
  fireEvent.change(input, { target: { value: '' } })
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить норматив' }))
  expect(client.updateOperationsSlaPolicy).toHaveBeenCalledWith(7, { target_hours: null })
  expect(screen.getByRole('button', { name: /Сохранить норматив/ })).toBeDisabled()
  await act(async () => pending.resolve({ park_id: 7, target_hours: null }))
  expect(screen.getByRole('status')).toHaveTextContent('Норматив сохранён')
  expect(changed).toHaveBeenCalledWith({ park_id: 7, target_hours: null })
})

it('does not deliver old park save success to the new owner', async () => {
  const pending = deferred<{ park_id: number; target_hours: number | null }>()
  const client = { operationsSlaPolicy: vi.fn(async (id: number) => ({ park_id: id, target_hours: 24 })), updateOperationsSlaPolicy: vi.fn(() => pending.promise) }
  const user = makeUser({ permissions: ['parks.manage'] })
  const changed = vi.fn()
  const view = render(<SlaPolicyEditor parkId={7} user={user} apiClient={client} onSaved={changed} />)
  await screen.findByLabelText('Норматив, часов')
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить норматив' }))
  view.rerender(<SlaPolicyEditor parkId={8} user={user} apiClient={client} onSaved={changed} />)
  await act(async () => pending.resolve({ park_id: 7, target_hours: 24 }))
  expect(changed).not.toHaveBeenCalled()
  expect(screen.queryByText('Норматив сохранён')).not.toBeInTheDocument()
})

it('keeps the editor visible and labels a save failure as a save failure', async () => {
  const client = {
    operationsSlaPolicy: vi.fn(async () => ({ park_id: 7, target_hours: 24 })),
    updateOperationsSlaPolicy: vi.fn(async () => { throw new Error('save failed') }),
  }
  render(<SlaPolicyEditor parkId={7} user={makeUser({ permissions: ['parks.manage'] })} apiClient={client} />)
  await screen.findByDisplayValue('24')
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить норматив' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось сохранить норматив SLA')
  expect(screen.getByLabelText('Норматив, часов')).toBeVisible()
})
