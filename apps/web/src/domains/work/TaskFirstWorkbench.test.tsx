import { render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { TaskFirstWorkbench } from './TaskFirstWorkbench'

describe.each([
  { enabled: false, primary: ['Задача', 'Проверка робота'] },
  { enabled: true, primary: ['Ремонт', 'Проверка', 'Чат'] },
])('TaskFirstWorkbench enabled=$enabled', ({ enabled, primary }) => {
  it('keeps workflow and related tasks in separate tab rows', () => {
    render(<TaskFirstWorkbench activeTab="task" canCheck enabled={enabled} focus="repair" onChange={vi.fn()} />)

    const workflow = screen.getByRole('tablist', { name: 'Разделы задачи' })
    const related = screen.getByRole('tablist', { name: 'Другие задачи робота' })
    expect(within(workflow).getAllByRole('tab').map(tab => tab.textContent)).toEqual(primary)
    expect(within(related).getAllByRole('tab').map(tab => tab.textContent)).toEqual([
      'Открытые задачи',
      'Закрытые задачи',
    ])
  })
})
