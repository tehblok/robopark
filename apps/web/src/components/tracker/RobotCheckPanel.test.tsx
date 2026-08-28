import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { ru } from '../../i18n/ru'
import { RobotCheckPanel } from './RobotCheckPanel'

describe('RobotCheckPanel', () => {
  it('renders nothing without a robot number', () => {
    const { container } = render(
      <MemoryRouter>
        <RobotCheckPanel robot={null} />
      </MemoryRouter>,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('links to Emergency and reports critical findings', async () => {
    render(
      <MemoryRouter>
        <RobotCheckPanel
          inspect={async () => ['ERROR: MCU']}
          robot="a1555"
        />
      </MemoryRouter>,
    )

    const link = screen.getByRole('link', {
      name: `${ru.tracker.robotCheck.open} a1555`,
    })
    expect(link).toHaveAttribute('href', '/emergency?q=a1555')

    await waitFor(() => {
      expect(screen.getByText('ERROR: MCU')).toBeInTheDocument()
    })
    expect(screen.getByText(ru.tracker.robotCheck.found)).toBeInTheDocument()
  })

  it('shows a clear state when Emergency has no critical errors', async () => {
    render(
      <MemoryRouter>
        <RobotCheckPanel inspect={async () => []} robot="447" />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByText(ru.tracker.robotCheck.noCritical)).toBeInTheDocument()
    })
  })
})
