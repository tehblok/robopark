import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, it } from 'vitest'
import { OperationsMetrics, OperationsTasks } from './OperationsPanels'
import { snapshot } from './operations.test-support'

it('renders an absent status count as unavailable instead of inventing zero', () => {
  const data = snapshot({ counts: { new: 2 } })
  render(<OperationsMetrics data={data} />)

  const moving = screen.getByText('Перемещение').closest<HTMLElement>('.rp-insights-metric')
  expect(moving).not.toBeNull()
  expect(within(moving!).getByText('Нет данных')).toBeVisible()
  expect(within(moving!).queryByText('0')).not.toBeInTheDocument()
})

it('does not infer total open load from the selected task list', () => {
  const data = snapshot({ counts: { new: 2 }, tasks_total: 1 })
  render(<MemoryRouter><OperationsTasks data={data} /></MemoryRouter>)

  expect(screen.getByText(/Нагрузка — Нет данных открытых задач/)).toBeVisible()
  expect(screen.queryByText(/Нагрузка — 1 открытых задач/)).not.toBeInTheDocument()
})
