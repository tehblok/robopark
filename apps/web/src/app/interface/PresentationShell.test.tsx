import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { PresentationShell } from './PresentationShell'

function slots() {
  return {
    navigation: <nav aria-label="Work navigation">Navigation</nav>,
    header: <header>Header</header>,
    content: <input aria-label="Live draft" type="file" />,
    context: <aside>Context</aside>,
    action: <button type="button">Action</button>,
  }
}

describe('PresentationShell', () => {
  it('renders every presentation zone through the classic shell', () => {
    const view = render(<PresentationShell mode="classic" slots={slots()} />)

    expect(screen.getByTestId('classic-shell')).toBeInTheDocument()
    for (const zone of ['navigation', 'header', 'content', 'context', 'action']) {
      expect(view.container.querySelector(`[data-shell-zone="${zone}"]`)).toBeInTheDocument()
    }
  })

  it('keeps the one live File owner across rerenders', () => {
    const sharedSlots = slots()
    const view = render(<PresentationShell mode="classic" slots={sharedSlots} />)
    const input = screen.getByLabelText('Live draft') as HTMLInputElement
    const file = new File(['photo'], 'repair.jpg', { type: 'image/jpeg' })
    fireEvent.change(input, { target: { files: [file] } })

    view.rerender(<PresentationShell mode="classic" slots={sharedSlots} />)

    expect(screen.getByLabelText('Live draft')).toBe(input)
    expect(input.files?.[0]).toBe(file)
    expect(view.container.querySelectorAll('input[type="file"]')).toHaveLength(1)
  })
})
