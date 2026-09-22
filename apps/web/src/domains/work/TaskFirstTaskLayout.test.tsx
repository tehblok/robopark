import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { TaskFirstTaskLayout } from './TaskFirstTaskLayout'

it('keeps the task flow without a duplicate context disclosure', () => {
  const { container } = render(<TaskFirstTaskLayout enabled header={<h1>Задача RP-1</h1>} action={<button type="button">Передать на проверку</button>}>
    <p>Что сейчас нужно сделать</p>
  </TaskFirstTaskLayout>)

  expect(screen.getByTestId('task-workflow-zone')).toHaveTextContent('Что сейчас нужно сделать')
  expect(screen.queryByTestId('task-context-zone')).not.toBeInTheDocument()
  expect(screen.queryByText('Контекст задачи')).not.toBeInTheDocument()
  expect(screen.getByTestId('task-action-zone')).toHaveTextContent('Передать на проверку')
  expect(container.querySelector('[data-task-header]')).toHaveTextContent('Задача RP-1')
  expect(container.querySelector('[data-task-body]')).toHaveTextContent('Что сейчас нужно сделать')
  const zones = Array.from(container.querySelectorAll('[data-task-zone]')).map(node => node.getAttribute('data-task-zone'))
  expect(zones).toEqual(['header', 'workflow', 'action'])
})

it('leaves Classic content structurally untouched', () => {
  const { container } = render(<TaskFirstTaskLayout enabled={false} action={<button type="button">Действие</button>}><p>Classic</p></TaskFirstTaskLayout>)
  expect(container).toHaveTextContent('Classic')
  expect(container.querySelector('[data-task-header]')).toBeInTheDocument()
  expect(container.querySelector('[data-task-body]')).toHaveTextContent('Classic')
  expect(container.querySelector('[data-task-zone]')).toBeNull()
})
