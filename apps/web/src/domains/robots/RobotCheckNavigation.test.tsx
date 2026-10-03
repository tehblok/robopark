import { useState } from 'react'
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

it('supports complete keyboard navigation in More and restores focus to the selected tab', async () => {
  const actor = userEvent.setup()
  function Harness() {
    const [activeId, setActiveId] = useState('state')
    return <RobotCheckNavigation activeId={activeId} onChange={setActiveId} tabs={tabs} />
  }
  render(<Harness />)

  await actor.click(screen.getByRole('button', { name: 'Ещё' }))
  expect(screen.getByRole('menuitem', { name: 'Карта' })).toHaveFocus()
  await actor.keyboard('{End}')
  expect(screen.getByRole('menuitem', { name: 'Колёса' })).toHaveFocus()
  await actor.keyboard('{ArrowUp}')
  expect(screen.getByRole('menuitem', { name: 'Задачи' })).toHaveFocus()
  await actor.keyboard('{Home}{ArrowDown}{Enter}')
  expect(screen.getByRole('tab', { name: 'Телеметрия' })).toHaveFocus()
})

it('restores focus when the already active secondary tab is selected again', async () => {
  const actor = userEvent.setup()
  render(<RobotCheckNavigation activeId="telemetry" onChange={vi.fn()} tabs={tabs} />)

  await actor.click(screen.getByRole('button', { name: 'Ещё' }))
  await actor.click(screen.getByRole('menuitem', { name: 'Телеметрия' }))

  expect(screen.getByRole('tab', { name: 'Телеметрия' })).toHaveFocus()
})
