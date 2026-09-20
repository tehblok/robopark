import { useRef, type CSSProperties, type KeyboardEvent, type ReactNode } from 'react'
import './Tabs.css'

export type TabItem = { id: string; label: string; count?: number }

export function Tabs({
  items,
  value,
  onChange,
  ariaLabel,
  panelIdFor,
}: {
  items: readonly TabItem[]
  value: string
  onChange: (id: string) => void
  ariaLabel: string
  panelIdFor: (id: string) => string
}) {
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([])
  const hasSelection = items.some(item => item.id === value)

  const selectAndFocus = (index: number) => {
    const item = items[index]
    if (!item) return
    onChange(item.id)
    tabRefs.current[index]?.focus()
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (items.length === 0) return

    let nextIndex: number | undefined
    if (event.key === 'ArrowRight') nextIndex = (index + 1) % items.length
    if (event.key === 'ArrowLeft') nextIndex = (index - 1 + items.length) % items.length
    if (event.key === 'Home') nextIndex = 0
    if (event.key === 'End') nextIndex = items.length - 1
    if (nextIndex === undefined) return

    event.preventDefault()
    selectAndFocus(nextIndex)
  }

  return (
    <div
      aria-label={ariaLabel}
      className="rp-tabs"
      role="tablist"
      style={{ '--rp-tab-count': items.length } as CSSProperties}
    >
      {items.map((item, index) => {
        const active = item.id === value
        return (
          <button
            aria-controls={panelIdFor(item.id)}
            aria-selected={active}
            className="rp-tabs__tab"
            id={`tab-${item.id}`}
            key={item.id}
            onClick={() => onChange(item.id)}
            onKeyDown={(event) => handleKeyDown(event, index)}
            ref={(element) => { tabRefs.current[index] = element }}
            role="tab"
            tabIndex={active || (!hasSelection && index === 0) ? 0 : -1}
            type="button"
          >
            <span>{item.label}</span>
            {item.count !== undefined ? (
              <span aria-hidden="true" className="rp-tabs__count">{item.count}</span>
            ) : null}
          </button>
        )
      })}
    </div>
  )
}

export function TabPanel({
  id,
  labelledBy,
  active,
  children,
}: {
  id: string
  labelledBy: string
  active: boolean
  children: ReactNode
}) {
  return (
    <div
      aria-labelledby={labelledBy}
      className="rp-tab-panel"
      hidden={!active}
      id={id}
      role="tabpanel"
    >
      {children}
    </div>
  )
}
