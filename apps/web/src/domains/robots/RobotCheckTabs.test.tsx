import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { RobotCheckTabs } from './RobotCheckTabs'
const tabs = [{ id: 'map', title: 'Карта', kind: 'map' as const }, { id: 'telemetry', title: 'Телеметрия', kind: 'telemetry' as const }, { id: 'scheme', title: 'Схема', kind: 'scheme' as const }]
it('roves focus and automatically activates wrapping arrows and Home/End', () => {
  const onChange = vi.fn(); render(<RobotCheckTabs tabs={tabs} activeId="map" onChange={onChange} />)
  const map = screen.getByRole('tab', { name: 'Карта' }); map.focus()
  expect(map).toHaveAttribute('aria-controls', 'robot-check-panel-map')
  fireEvent.keyDown(map, { key: 'ArrowLeft' }); expect(screen.getByRole('tab', { name: 'Схема' })).toHaveFocus()
  fireEvent.keyDown(document.activeElement!, { key: 'Home' }); expect(map).toHaveFocus()
  fireEvent.keyDown(map, { key: 'ArrowRight' }); expect(screen.getByRole('tab', { name: 'Телеметрия' })).toHaveFocus()
  fireEvent.keyDown(document.activeElement!, { key: 'End' }); expect(onChange).toHaveBeenLastCalledWith('scheme')
  expect(screen.getByRole('tablist', { name: 'Разделы проверки робота' })).toBeInTheDocument()
})
