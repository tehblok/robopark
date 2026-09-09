import { fireEvent, render, screen, within } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import type { Park } from '../../api'
import { ParkMultiSelect } from './ParkMultiSelect'

const parks: Park[] = [
  { id: 3, name: 'Юг', tag: 'south', is_active: true },
  { id: 1, name: 'Север', tag: 'north', is_active: true },
  { id: 2, name: 'Архив', tag: 'archive', is_active: false },
]

it('selects active parks, filters choices, and returns sorted unique IDs', () => {
  const onChange = vi.fn()
  render(<ParkMultiSelect label="Парки" onChange={onChange} parks={parks} value={[3]} />)

  fireEvent.click(screen.getByRole('button', { name: /Парки.*Выбрано: 1/ }))
  const listbox = screen.getByRole('listbox', { name: 'Парки' })
  expect(listbox).toHaveAttribute('aria-multiselectable', 'true')
  fireEvent.change(screen.getByRole('textbox', { name: 'Поиск парков' }), { target: { value: 'сев' } })
  fireEvent.click(within(listbox).getByRole('checkbox', { name: 'Север' }))

  expect(onChange).toHaveBeenLastCalledWith([1, 3])
})

it('keeps an assigned inactive park visible but prevents selecting it anew', () => {
  render(<ParkMultiSelect label="Парки" onChange={vi.fn()} parks={parks} value={[2]} />)
  fireEvent.click(screen.getByRole('button', { name: /Парки.*Выбрано: 1/ }))

  expect(screen.getByRole('checkbox', { name: /Архив.*неактивен/ })).toBeChecked()
  expect(screen.getByRole('checkbox', { name: /Архив.*неактивен/ })).not.toBeDisabled()
  expect(screen.getByRole('textbox', { name: 'Поиск парков' })).toHaveFocus()
})

it('disables an inactive park that is not already assigned', () => {
  render(<ParkMultiSelect label="Парки" onChange={vi.fn()} parks={parks} value={[]} />)
  fireEvent.click(screen.getByRole('button', { name: /Парки.*Выбрано: 0/ }))

  expect(screen.getByRole('checkbox', { name: /Архив.*неактивен/ })).toBeDisabled()
})

it('selects all active parks, clears selections, and closes on Escape or outside click', () => {
  const onChange = vi.fn()
  render(<><ParkMultiSelect label="Парки" onChange={onChange} parks={parks} value={[2]} /><button type="button">Вне</button></>)
  fireEvent.click(screen.getByRole('button', { name: /Парки.*Выбрано: 1/ }))
  fireEvent.click(screen.getByRole('button', { name: 'Выбрать все' }))
  expect(onChange).toHaveBeenLastCalledWith([1, 2, 3])
  fireEvent.click(screen.getByRole('button', { name: 'Очистить' }))
  expect(onChange).toHaveBeenLastCalledWith([])
  fireEvent.keyDown(screen.getByRole('listbox'), { key: 'Escape' })
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /Парки.*Выбрано: 1/ }))
  fireEvent.mouseDown(screen.getByRole('button', { name: 'Вне' }))
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
})

it('does not open or focus controls when disabled', () => {
  render(<ParkMultiSelect disabled label="Парки" onChange={vi.fn()} parks={parks} value={[]} />)
  const trigger = screen.getByRole('button', { name: /Парки.*Выбрано: 0/ })
  expect(trigger).toBeDisabled()
  fireEvent.click(trigger)
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
})
