import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { PageLayout, Panel } from './PageLayout'

describe('PageLayout', () => {
  it('renders page context, title, actions, and content in one layout', () => {
    render(
      <PageLayout
        actions={<button type="button">Обновить</button>}
        description="Текущая смена"
        eyebrow="Парк: Север"
        title="Обзор"
      >
        <p>Содержимое</p>
      </PageLayout>,
    )

    expect(screen.getByRole('heading', { level: 1, name: 'Обзор' })).toBeVisible()
    expect(screen.getByText('Парк: Север')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Обновить' })).toBeVisible()
    expect(screen.getByText('Содержимое')).toBeVisible()
  })
})

describe('Panel', () => {
  it('names a titled panel region from its heading', () => {
    render(
      <Panel actions={<button type="button">Ещё</button>} description="Детали" title="Риск">
        <p>Статус</p>
      </Panel>,
    )

    expect(screen.getByRole('region', { name: 'Риск' })).toHaveTextContent('Детали')
  })

  it('does not invent an accessible name without a title', () => {
    const { container } = render(<Panel><p>Статус</p></Panel>)

    expect(container.querySelector('section')).not.toHaveAttribute('aria-labelledby')
    expect(screen.queryByRole('region')).not.toBeInTheDocument()
  })
})
