import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { TabPanel, Tabs } from './Tabs'

const items = [
  { id: 'overview', label: 'Обзор' },
  { id: 'robots', label: 'Роботы' },
  { id: 'settings', label: 'Настройки' },
] as const

describe('Tabs', () => {
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
