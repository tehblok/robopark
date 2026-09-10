import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { RobotCheckNavigation } from './RobotCheckNavigation'
import type { RobotCheckTab } from './robotCheckUrl'

const tabs: RobotCheckTab[] = [
  { id: 'map', title: 'Карта', kind: 'map' },
  { id: 'state', title: 'Состояние', kind: 'state' },
  { id: 'errors', title: 'Ошибки', kind: 'errors' },
  { id: 'telemetry', title: 'Телеметрия', kind: 'telemetry' },
  { id: 'tasks', title: 'Задачи', kind: 'tasks' },
  { id: 'history', title: 'История', kind: 'history' },
  { id: 'scheme', title: 'Схема', kind: 'scheme' },
  { id: 'wheels', title: 'Колёса', kind: 'section' },
]

it('keeps three primary tabs and selects secondary sections through More as real tabs', async () => {
  const actor = userEvent.setup()
  const onChange = vi.fn()
  const view = render(<RobotCheckNavigation tabs={tabs} activeId="state" onChange={onChange} />)

  expect(screen.getAllByRole('tab').map(tab => tab.textContent)).toEqual(['Состояние', 'Ошибки', 'Схема'])
  await actor.click(screen.getByRole('button', { name: 'Ещё' }))
  await actor.click(screen.getByRole('menuitem', { name: 'Телеметрия' }))
  expect(onChange).toHaveBeenCalledWith('telemetry')

  view.rerender(<RobotCheckNavigation tabs={tabs} activeId="telemetry" onChange={onChange} />)
  expect(screen.getAllByRole('tab').map(tab => tab.textContent)).toEqual(['Состояние', 'Ошибки', 'Схема', 'Телеметрия'])
  expect(screen.getByRole('tab', { name: 'Телеметрия' })).toHaveAttribute('aria-selected', 'true')
})
