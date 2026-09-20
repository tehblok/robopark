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

it('scopes shared shell controls beneath an explicit presentation shell', () => {
  const source = readFileSync(resolve('src/app/shell/AppShell.css'), 'utf8')
  const unscoped = source.split('\n').filter(line => /^\s*(?:\.|button\.)/.test(line))
  const scoped = source.match(/:is\(\.rp-classic-shell, \.rp-task-first-shell\)/g) ?? []

  expect(scoped.length).toBeGreaterThan(20)
  expect(unscoped).toEqual([])
})
