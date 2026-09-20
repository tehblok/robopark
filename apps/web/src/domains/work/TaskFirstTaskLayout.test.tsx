import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { TaskFirstTaskLayout } from './TaskFirstTaskLayout'

it('renders the promised A workflow, context and bottom action as separate zones', () => {
  render(<TaskFirstTaskLayout enabled context={<p>Робот сейчас</p>} action={<button type="button">Передать на проверку</button>}>
    <p>Что сейчас нужно сделать</p>
  </TaskFirstTaskLayout>)

  expect(screen.getByTestId('task-workflow-zone')).toHaveTextContent('Что сейчас нужно сделать')
  expect(screen.getByTestId('task-context-zone')).toHaveTextContent('Робот сейчас')
  expect(screen.getByTestId('task-action-zone')).toHaveTextContent('Передать на проверку')
  expect(screen.getByText('Контекст задачи').closest('details')).not.toHaveAttribute('open')
})

it('leaves Classic content structurally untouched', () => {
  const { container } = render(<TaskFirstTaskLayout enabled={false} context={<p>Контекст</p>} action={<button type="button">Действие</button>}><p>Classic</p></TaskFirstTaskLayout>)
  expect(container).toHaveTextContent('Classic')
  expect(container.querySelector('[data-task-zone]')).toBeNull()
  expect(container).not.toHaveTextContent('Контекст')
})
