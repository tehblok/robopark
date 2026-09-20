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

  it('links to the canonical robot check and reports critical findings', async () => {
    render(
      <MemoryRouter>
        <RobotCheckPanel
          inspect={async () => ['ERROR: MCU']}
          robot="a1555"
        />
      </MemoryRouter>,
    )

    const link = screen.getByRole('link', {
      name: 'Проверить робота a1555',
    })
    expect(link).toHaveAttribute('href', '/robots/a1555/check')

    await waitFor(() => {
      expect(screen.getByText('ERROR: MCU')).toBeInTheDocument()
    })
    expect(screen.getByText(ru.tracker.robotCheck.found)).toBeInTheDocument()
  })

  it('trims and encodes the robot route segment once', () => {
    render(
      <MemoryRouter>
        <RobotCheckPanel inspect={async () => []} robot=" A/42 ?# " />
      </MemoryRouter>,
    )

    expect(screen.getByRole('link', { name: 'Проверить робота A/42 ?#' })).toHaveAttribute(
      'href',
      '/robots/A%2F42%20%3F%23/check',
    )
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

  it('uses the latest inspection function when the robot changes', async () => {
    const view = render(
      <MemoryRouter>
        <RobotCheckPanel inspect={async () => ['old result']} robot="447" />
      </MemoryRouter>,
    )
    expect(await screen.findByText('old result')).toBeInTheDocument()

    view.rerender(
      <MemoryRouter>
        <RobotCheckPanel inspect={async () => ['new result']} robot="448" />
      </MemoryRouter>,
    )

    expect(await screen.findByText('new result')).toBeInTheDocument()
    expect(screen.queryByText('old result')).not.toBeInTheDocument()
  })
})
