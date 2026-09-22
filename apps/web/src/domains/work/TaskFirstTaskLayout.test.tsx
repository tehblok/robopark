import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { TaskFirstTaskLayout } from './TaskFirstTaskLayout'

it('keeps robot context in a disclosure instead of a separate A column', () => {
  const { container } = render(<TaskFirstTaskLayout enabled header={<h1>Задача RP-1</h1>} context={<p>Робот сейчас</p>} action={<button type="button">Передать на проверку</button>}>
    <p>Что сейчас нужно сделать</p>
  </TaskFirstTaskLayout>)

  expect(screen.getByTestId('task-workflow-zone')).toHaveTextContent('Что сейчас нужно сделать')
  expect(screen.queryByTestId('task-context-zone')).not.toBeInTheDocument()
  expect(screen.getByText('Контекст задачи').closest('details')).toHaveTextContent('Робот сейчас')
  expect(screen.getByTestId('task-action-zone')).toHaveTextContent('Передать на проверку')
  expect(screen.getByText('Контекст задачи').closest('details')).not.toHaveAttribute('open')
  expect(container.querySelector('[data-task-header]')).toHaveTextContent('Задача RP-1')
  expect(container.querySelector('[data-task-body]')).toHaveTextContent('Что сейчас нужно сделать')
  const zones = Array.from(container.querySelectorAll('[data-task-zone]')).map(node => node.getAttribute('data-task-zone'))
  expect(zones).toEqual(['header', 'workflow', 'action'])
})

it('leaves Classic content structurally untouched', () => {
  const { container } = render(<TaskFirstTaskLayout enabled={false} context={<p>Контекст</p>} action={<button type="button">Действие</button>}><p>Classic</p></TaskFirstTaskLayout>)
  expect(container).toHaveTextContent('Classic')
  expect(container.querySelector('[data-task-header]')).toBeInTheDocument()
  expect(container.querySelector('[data-task-body]')).toHaveTextContent('Classic')
  expect(container.querySelector('[data-task-zone]')).toBeNull()
  expect(container).not.toHaveTextContent('Контекст')
})
