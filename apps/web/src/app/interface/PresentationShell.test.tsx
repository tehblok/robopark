/// <reference types="node" />

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
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
  it('exposes all presentation zones in task-first and classic shells', () => {
    const view = render(<PresentationShell mode="task-first" slots={slots()} />)

    expect(screen.getByTestId('task-first-shell')).toBeInTheDocument()
    for (const zone of ['navigation', 'header', 'content', 'context', 'action']) {
      expect(view.container.querySelector(`[data-shell-zone="${zone}"]`)).toBeInTheDocument()
    }

    view.rerender(<PresentationShell mode="classic" slots={slots()} />)
    expect(screen.getByTestId('classic-shell')).toBeInTheDocument()
    expect(view.container.querySelectorAll('[data-shell-zone="content"]')).toHaveLength(1)
  })

  it('uses mode-owned slot wrappers instead of a shared page-layout structure', () => {
    const view = render(<PresentationShell mode="classic" slots={slots()} />)

    expect(view.container.querySelector('.rp-classic-shell__workspace')).toBeInTheDocument()
    expect(view.container.querySelector('.rp-classic-shell__content')).toHaveAttribute('data-shell-zone', 'content')
    expect(view.container.querySelector('.rp-task-first-shell__workspace')).not.toBeInTheDocument()
    expect(view.container.querySelector('.app-shell, .app-main, .app-content')).not.toBeInTheDocument()

    view.rerender(<PresentationShell mode="task-first" slots={slots()} />)

    expect(view.container.querySelector('.rp-task-first-shell__workspace')).toBeInTheDocument()
    expect(view.container.querySelector('.rp-task-first-shell__content')).toHaveAttribute('data-shell-zone', 'content')
    expect(view.container.querySelector('.rp-classic-shell__workspace')).not.toBeInTheDocument()
    expect(view.container.querySelector('.app-shell, .app-main, .app-content')).not.toBeInTheDocument()
  })

  it('keeps the one live File owner when presentation changes', () => {
    const sharedSlots = slots()
    const view = render(<PresentationShell mode="classic" slots={sharedSlots} />)
    const input = screen.getByLabelText('Live draft') as HTMLInputElement
    const file = new File(['photo'], 'repair.jpg', { type: 'image/jpeg' })
    fireEvent.change(input, { target: { files: [file] } })

    view.rerender(<PresentationShell mode="task-first" slots={sharedSlots} />)

    expect(screen.getByLabelText('Live draft')).toBe(input)
    expect(input.files?.[0]).toBe(file)
    expect(view.container.querySelectorAll('input[type="file"]')).toHaveLength(1)
  })
})

it('scopes structural interface A CSS to the task-first shell', () => {
  const source = readFileSync(resolve('src/app/interface/interface-a.css'), 'utf8')

  expect(source).toMatch(/^\.rp-task-first-shell\s*\{/m)
  expect(source).not.toMatch(/^html\[data-interface=['"]task-first['"]\]\s*\{/m)
  expect(source).not.toMatch(/^\.(?:panel|page)(?:\s|[>{.:#])/m)
})

it('keeps page structure mode-owned and portal controls layout-neutral', () => {
  const shared = readFileSync(resolve('src/app/shell/AppShell.css'), 'utf8')
  const classic = readFileSync(resolve('src/app/interface/ClassicShell.css'), 'utf8')
  const taskFirst = readFileSync(resolve('src/app/interface/TaskFirstShell.css'), 'utf8')

  expect(shared).not.toMatch(/:is\(\.rp-classic-shell, \.rp-task-first-shell\)/)
  expect(shared).not.toMatch(/min-height:\s*100dvh|grid-template-columns:/)
  expect(shared).toMatch(/\.rp-shell-controls/)
  expect(classic).toMatch(/\.rp-classic-shell\s*\{[^}]*grid-template-columns:\s*17rem/s)
  expect(taskFirst).toMatch(/\.rp-task-first-shell\s*\{[^}]*grid-template-columns:\s*224px/s)
  expect(classic).not.toMatch(/\.rp-task-first-shell/)
  expect(taskFirst).not.toMatch(/\.rp-classic-shell/)
})
