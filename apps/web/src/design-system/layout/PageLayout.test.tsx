/// <reference types="node" />

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fireEvent, render, screen } from '@testing-library/react'
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

    expect(screen.getByRole('heading', { level: 1, name: 'Обзор' })).toHaveClass(
      'rp-page-layout__title',
    )
    expect(screen.getByText('Парк: Север')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Обновить' })).toBeVisible()
    expect(screen.getByText('Содержимое')).toBeVisible()
  })

  it('does not create a second main landmark inside the application shell main', () => {
    const { container } = render(
      <main id="main-content">
        <PageLayout title="Обзор">
          <p>Содержимое</p>
        </PageLayout>
      </main>,
    )

    expect(container.querySelectorAll('main')).toHaveLength(1)
    expect(screen.getByRole('heading', { level: 1, name: 'Обзор' })).toHaveClass(
      'rp-page-layout__title',
    )
  })
})

it('styles only headings owned by the layout primitives', () => {
  const source = readFileSync(resolve('src/design-system/layout/PageLayout.css'), 'utf8')

  expect(source).toContain('.rp-page-layout__title')
  expect(source).toContain('.rp-panel__title')
  expect(source).not.toMatch(/\.rp-page-layout\s+h1/)
  expect(source).not.toMatch(/\.rp-panel\s+h2/)
})

it('leaves horizontal page gutters to the selected presentation shell', () => {
  const source = readFileSync(resolve('src/design-system/layout/PageLayout.css'), 'utf8')

  expect(source).not.toMatch(/\.rp-page-layout\s*\{[^}]*\bpadding\s*:/s)
  expect(source).not.toMatch(/\.rp-page-layout\s*\{[^}]*\bpadding-inline\s*:/s)
})

it('uses the exact Classic geometry tokens at desktop, tablet, and phone widths', () => {
  const interfaceTokens = readFileSync(
    resolve('src/app/interface/interfaceTokens.css'),
    'utf8',
  )
  const designTokens = readFileSync(resolve('src/design-system/styles/tokens.css'), 'utf8')

  expect(interfaceTokens).toMatch(/:root\s*\{[^}]*--rp-page-gutter:\s*24px/s)
  expect(interfaceTokens).toMatch(/:root\s*\{[^}]*--rp-section-gap:\s*24px/s)
  expect(interfaceTokens).toMatch(/:root\s*\{[^}]*--rp-card-padding:\s*20px/s)
  expect(interfaceTokens).toMatch(/@media \(min-width:\s*600px\) and \(max-width:\s*899px\)\s*\{[^}]*:root\s*\{[^}]*--rp-page-gutter:\s*16px/s)
  expect(interfaceTokens).toMatch(/@media \(max-width:\s*599px\)\s*\{[^}]*:root\s*\{[^}]*--rp-page-gutter:\s*12px/s)
  expect(interfaceTokens).toMatch(/@media \(max-width:\s*899px\)\s*\{[^}]*:root\s*\{[^}]*--rp-section-gap:\s*16px/s)
  expect(interfaceTokens).toMatch(/@media \(max-width:\s*899px\)\s*\{[^}]*:root\s*\{[^}]*--rp-card-padding:\s*16px/s)
  expect(designTokens).toMatch(/--rp-control-min-size:\s*44px/)
  expect(designTokens).toMatch(/--rp-touch-primary-min-size:\s*48px/)
  expect(designTokens).toMatch(/--rp-control-gap:\s*8px/)
  expect(designTokens).toMatch(/--rp-action-gap:\s*12px/)
  expect(designTokens).toMatch(/--rp-compact-row-padding:\s*12px/)
  expect(designTokens).toMatch(/--rp-radius-card:\s*16px/)
  expect(designTokens).toMatch(/--rp-radius-control:\s*12px/)
  expect(designTokens).toMatch(/--rp-radius-chip:\s*999px/)
})

it('contains flexible children and separates nested panel surfaces without negative margins', () => {
  const css = readFileSync(resolve('src/design-system/layout/PageLayout.css'), 'utf8')
  const shellCss = readFileSync(resolve('src/app/interface/ClassicShell.css'), 'utf8')

  expect(css).toMatch(/\.rp-panel\s*\{[^}]*gap:\s*var\(--rp-form-gap\)/s)
  expect(css).toMatch(/\.rp-page-layout[^\{]*\{[^}]*min-inline-size:\s*0/s)
  expect(css).toMatch(/\.rp-page-layout__content > \*,[^\{]*\.rp-panel__content > \*\s*\{[^}]*min-inline-size:\s*0/s)
  expect(css).toMatch(/\.rp-panel__content:has\(> \.rp-panel\)\s*\{[^}]*gap:\s*var\(--rp-form-gap\)/s)
  expect(css).toMatch(/\.rp-panel \.rp-panel\s*\{[^}]*background:\s*var\(--rp-surface-elevated\)/s)
  expect(css).not.toMatch(/margin:\s*-\d/)
  expect(shellCss).toMatch(/\.rp-classic-shell > \*\s*\{[^}]*min-inline-size:\s*0/s)
  expect(shellCss).not.toMatch(/margin:\s*-\d/)
})

describe('Panel', () => {
  it('uses the saved fallback when no collapse preference exists', () => {
    render(
      <Panel collapsible defaultCollapsed storageKey="secondary" title="Secondary">
        Body
      </Panel>,
    )

    expect(screen.queryByText('Body')).not.toBeInTheDocument()
  })

  it('persists an expanded preference when the fallback is collapsed', () => {
    localStorage.removeItem('robopark:panel:secondary-expanded:collapsed')
    const { unmount } = render(
      <Panel collapsible defaultCollapsed storageKey="secondary-expanded" title="Secondary">
        Body
      </Panel>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Развернуть: Secondary' }))
    unmount()
    render(
      <Panel collapsible defaultCollapsed storageKey="secondary-expanded" title="Secondary">
        Body
      </Panel>,
    )

    expect(screen.getByText('Body')).toBeVisible()
  })

  it('exposes its content density', () => {
    render(<Panel density="dense">Body</Panel>)

    expect(screen.getByText('Body').closest('section')).toHaveAttribute('data-density', 'dense')
  })

  it('rejects a collapsible panel without an explicit identity in development', () => {
    expect(() => render(<Panel collapsible title="Диагностика"><p>Данные</p></Panel>)).toThrow(
      'A collapsible Panel requires a nonempty string title and storageKey.',
    )
  })

  it('collapses accessible content and persists the selected state', () => {
    render(
      <Panel collapsible storageKey="diagnostics" title="Диагностика">
        <p>Данные</p>
      </Panel>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Свернуть: Диагностика' }))

    expect(screen.queryByText('Данные')).not.toBeInTheDocument()
    expect(localStorage.getItem('robopark:panel:diagnostics:collapsed')).toBe('1')
    const toggle = screen.getByRole('button', { name: 'Развернуть: Диагностика' })
    expect(toggle).toHaveTextContent('Развернуть')
    expect(toggle).not.toHaveTextContent('Диагностика')
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(toggle).not.toHaveAttribute('aria-controls')
  })

  it('names a titled panel region from its heading', () => {
    render(
      <Panel actions={<button type="button">Ещё</button>} description="Детали" title="Риск">
        <p>Статус</p>
      </Panel>,
    )

    expect(screen.getByRole('heading', { level: 2, name: 'Риск' })).toHaveClass('rp-panel__title')
    expect(screen.getByRole('region', { name: 'Риск' })).toHaveTextContent('Детали')
  })

  it('does not invent an accessible name without a title', () => {
    const { container } = render(<Panel><p>Статус</p></Panel>)

    expect(container.querySelector('section')).not.toHaveAttribute('aria-labelledby')
    expect(screen.queryByRole('region')).not.toBeInTheDocument()
  })
})
