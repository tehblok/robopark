import { render, screen, within } from '@testing-library/react'
import { expect, it } from 'vitest'
import { FlowChart } from './FlowChart'
import { snapshot } from './operations.test-support'

it('keeps observed zeros and a missing bucket distinct in SVG and table, labels Moscow', () => {
  const { container } = render(<FlowChart flow={snapshot().flow} />)
  const table = screen.getByRole('table', { name: /Поток задач/ })
  const rows = within(table).getAllByRole('row')
  expect(rows).toHaveLength(4)
  expect(rows[1]).toHaveTextContent('02.09.2026, 23:00')
  expect(within(rows[1]).getAllByRole('cell').map(cell => cell.textContent)).toEqual(['0', '0'])
  expect(rows[2]).toHaveTextContent('03.09.2026, 01:00')
  expect(within(rows[2]).getAllByRole('cell').map(cell => cell.textContent)).toEqual(['Нет данных', 'Нет данных'])
  expect(within(rows[3]).getAllByRole('cell').map(cell => cell.textContent)).toEqual(['3', '2'])
  expect(container.querySelectorAll('circle[data-series="arrivals"]')).toHaveLength(2)
  expect([...container.querySelectorAll('circle[data-series="arrivals"]')].map(node => node.getAttribute('data-value'))).toEqual(['0', '3'])
  expect(container.querySelectorAll('polyline')).toHaveLength(4)
  expect(screen.getByText(/2 из 3/)).toBeVisible()
})

it('does not turn absent history into zero-valued throughput', () => {
  render(<FlowChart flow={{ ...snapshot().flow, points: [], observed_buckets: 0 }} />)
  expect(screen.getByText('История пока не накоплена')).toBeVisible()
  expect(screen.queryByRole('img')).not.toBeInTheDocument()
  expect(screen.queryByRole('table')).not.toBeInTheDocument()
})
