/// <reference types="node" />

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { TabPanel, Tabs } from './Tabs'

const items = [
  { id: 'overview', label: 'Обзор' },
  { id: 'robots', label: 'Роботы' },
  { id: 'settings', label: 'Настройки' },
] as const

describe('Tabs', () => {
  it('exposes primary and secondary variants without changing the horizontal keyboard contract', () => {
    const onChange = vi.fn()
    const { rerender } = render(
      <Tabs
        ariaLabel="Основные разделы"
        items={items}
        onChange={onChange}
        panelIdFor={(id) => `panel-${id}`}
        value="overview"
        variant="primary"
      />,
    )

    const first = screen.getByRole('tab', { name: 'Обзор' })
    fireEvent.keyDown(first, { key: 'End' })
    expect(onChange).toHaveBeenLastCalledWith('settings')
    expect(screen.getByRole('tablist')).toHaveClass('rp-tabs--primary')
    expect(screen.getByRole('tablist')).toHaveAttribute('aria-orientation', 'horizontal')

    rerender(
      <Tabs
        ariaLabel="Фильтр"
        items={items}
        onChange={onChange}
        panelIdFor={(id) => `panel-${id}`}
        value="overview"
        variant="secondary"
      />,
    )
    expect(screen.getByRole('tablist')).toHaveClass('rp-tabs--secondary')
  })

  it('contains primary scrolling and secondary wrapping inside the tabs box', () => {
    const css = readFileSync(resolve('src/design-system/navigation/Tabs.css'), 'utf8')

    expect(css).toMatch(/\.rp-tabs\s*\{[^}]*max-inline-size:\s*100%[^}]*min-inline-size:\s*0/s)
    expect(css).toMatch(/\.rp-tabs--primary\s*\{[^}]*border:\s*1px solid var\(--rp-border\)[^}]*border-radius:\s*var\(--rp-radius-control\)[^}]*overflow-x:\s*auto/s)
    expect(css).toMatch(/\.rp-tabs--secondary\s*\{[^}]*flex-wrap:\s*wrap[^}]*overflow-x:\s*hidden/s)
    expect(css).toMatch(/\.rp-tabs--secondary \.rp-tabs__tab\s*\{[^}]*max-inline-size:\s*100%[^}]*overflow-wrap:\s*anywhere/s)
    expect(css).toMatch(/\.rp-tabs__count\s*\{[^}]*border-radius:\s*var\(--rp-radius-chip\)/s)
    expect(css).not.toMatch(/margin:\s*-\d/)
  })

  it('keeps a keyboard entry when selection belongs to a related section', () => {
    render(<Tabs ariaLabel="Разделы" items={items} value="related" onChange={vi.fn()} panelIdFor={id => `panel-${id}`} />)
    expect(screen.getByRole('tab', { name: 'Обзор' })).toHaveAttribute('tabindex', '0')
    expect(screen.getAllByRole('tab').filter(tab => tab.tabIndex === 0)).toHaveLength(1)
    expect(screen.getByRole('tab', { name: 'Обзор' })).toHaveAttribute('aria-selected', 'false')
  })
  it('links each selected tab to its labelled panel with stable IDs', () => {
    render(
      <>
        <Tabs
          ariaLabel="Разделы парка"
          items={items}
          onChange={vi.fn()}
          panelIdFor={(id) => `panel-${id}`}
          value="overview"
        />
        <TabPanel active id="panel-overview" labelledBy="tab-overview">
          Сводка
        </TabPanel>
      </>,
    )

    const tablist = screen.getByRole('tablist', { name: 'Разделы парка' })
    const tab = screen.getByRole('tab', { name: 'Обзор' })
    const panel = screen.getByRole('tabpanel')
    expect(tablist).toContainElement(tab)
    expect(tablist).toHaveStyle({ '--rp-tab-count': '3' })
    expect(tab).toHaveAttribute('id', 'tab-overview')
    expect(tab).toHaveAttribute('aria-controls', 'panel-overview')
    expect(tab).toHaveAttribute('aria-selected', 'true')
    expect(panel).toHaveAttribute('id', 'panel-overview')
    expect(panel).toHaveAttribute('aria-labelledby', 'tab-overview')
  })

  it('moves selection and DOM focus to the next tab on ArrowRight', () => {
    const onChange = vi.fn()
    render(
      <Tabs
        ariaLabel="Разделы парка"
        items={items}
        onChange={onChange}
        panelIdFor={(id) => `panel-${id}`}
        value="overview"
      />,
    )
    const active = screen.getByRole('tab', { name: 'Обзор' })
    const next = screen.getByRole('tab', { name: 'Роботы' })

    active.focus()
    fireEvent.keyDown(active, { key: 'ArrowRight' })

    expect(onChange).toHaveBeenCalledWith('robots')
    expect(next).toHaveFocus()
  })

  it('wraps ArrowLeft and supports Home and End roving focus', () => {
    const onChange = vi.fn()
    render(
      <Tabs
        ariaLabel="Разделы парка"
        items={items}
        onChange={onChange}
        panelIdFor={(id) => `panel-${id}`}
        value="overview"
      />,
    )
    const first = screen.getByRole('tab', { name: 'Обзор' })
    const middle = screen.getByRole('tab', { name: 'Роботы' })
    const last = screen.getByRole('tab', { name: 'Настройки' })

    first.focus()
    fireEvent.keyDown(first, { key: 'ArrowLeft' })
    expect(onChange).toHaveBeenLastCalledWith('settings')
    expect(last).toHaveFocus()

    middle.focus()
    fireEvent.keyDown(middle, { key: 'Home' })
    expect(onChange).toHaveBeenLastCalledWith('overview')
    expect(first).toHaveFocus()

    fireEvent.keyDown(first, { key: 'End' })
    expect(onChange).toHaveBeenLastCalledWith('settings')
    expect(last).toHaveFocus()
  })
})
