import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { RepairComponentPicker } from './RepairComponentPicker'

it('finds a readable part by a mechanic synonym or its original Tracker name', () => {
  const onChange = vi.fn()
  render(<RepairComponentPicker options={[
    { id: '42', label: 'Аккумулятор', tracker_name: 'ROBOT_BATTERY', aliases: ['АКБ', 'батарея'] },
    { id: '43', label: 'Камера' },
  ]} value={[]} onChange={onChange} />)
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'акб' } })
  fireEvent.click(screen.getByRole('checkbox', { name: 'Аккумулятор' }))
  expect(onChange).toHaveBeenCalledWith(['42'])
  expect(screen.queryByRole('checkbox', { name: 'Камера' })).not.toBeInTheDocument()
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'ROBOT_BATTERY' } })
  expect(screen.getByRole('checkbox', { name: 'Аккумулятор' })).toBeVisible()
})
