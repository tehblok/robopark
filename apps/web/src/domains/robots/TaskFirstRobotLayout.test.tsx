import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { TaskFirstRobotLayout } from './TaskFirstRobotLayout'

it('uses the A composition only for interface A', () => {
  const { rerender } = render(<TaskFirstRobotLayout taskFirst identity={<p>Робот</p>} detail={<p>Диагностика</p>} />)
  expect(screen.getByTestId('robot-check-layout')).toHaveClass('a-robot-layout')
  expect(screen.getByTestId('robot-check-layout')).not.toHaveClass('classic-robot-layout')

  rerender(<TaskFirstRobotLayout taskFirst={false} identity={<p>Робот</p>} detail={<p>Диагностика</p>} />)
  expect(screen.getByTestId('robot-check-layout')).toHaveClass('classic-robot-layout')
  expect(screen.getByTestId('robot-check-layout')).not.toHaveClass('a-robot-layout')
})

it('exposes stable overview and diagnostic geometry regions in both interfaces', () => {
  const { rerender } = render(<TaskFirstRobotLayout taskFirst identity={<p>Робот</p>} detail={<p>Диагностика</p>} />)

  expect(screen.getByTestId('robot-check-layout').querySelector('[data-robot-overview]')).toBeInTheDocument()
  expect(screen.getByTestId('robot-check-layout').querySelector('[data-robot-details]')).toBeInTheDocument()

  rerender(<TaskFirstRobotLayout taskFirst={false} identity={<p>Робот</p>} detail={<p>Диагностика</p>} />)
  expect(screen.getByTestId('robot-check-layout').querySelector('[data-robot-overview]')).toBeInTheDocument()
  expect(screen.getByTestId('robot-check-layout').querySelector('[data-robot-details]')).toBeInTheDocument()
})
