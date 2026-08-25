import type { ReactNode } from 'react'

export type TabItem = {
  id: string
  label: string
  /** Optional counter shown next to the label (pending requests, …). */
  count?: number
}

/**
 * Tab bar used to split long admin screens into sections.
 *
 * Implemented with the `tablist`/`tab` roles and arrow-key navigation so the
 * screen stays usable from the keyboard.
 */
export function Tabs({
  items,
  value,
  onChange,
}: {
  items: TabItem[]
  value: string
  onChange: (id: string) => void
}) {
  const move = (delta: number) => {
    const index = items.findIndex((item) => item.id === value)
    if (index < 0) return
    const next = items[(index + delta + items.length) % items.length]
    onChange(next.id)
  }

  return (
    <div className="tabs" role="tablist">
      {items.map((item) => {
        const active = item.id === value
        return (
          <button
            aria-selected={active}
            className={`tab${active ? ' is-active' : ''}`}
            key={item.id}
            onClick={() => onChange(item.id)}
            onKeyDown={(event) => {
              if (event.key === 'ArrowRight') {
                event.preventDefault()
                move(1)
              }
              if (event.key === 'ArrowLeft') {
                event.preventDefault()
                move(-1)
              }
            }}
            role="tab"
            tabIndex={active ? 0 : -1}
            type="button"
          >
            {item.label}
            {item.count != null && item.count > 0 && (
              <span className="tab-count">{item.count}</span>
            )}
          </button>
        )
      })}
    </div>
  )
}

export function TabPanel({
  active,
  children,
}: {
  active: boolean
  children: ReactNode
}) {
  if (!active) return null
  return (
    <div className="tab-panel animate-in" role="tabpanel">
      {children}
    </div>
  )
}

/** Accessible on/off switch — clearer than a button labelled «Переключить». */
export function Toggle({
  checked,
  disabled,
  label,
  onChange,
}: {
  checked: boolean
  disabled?: boolean
  label: string
  onChange: (next: boolean) => void
}) {
  return (
    <label className={`toggle${disabled ? ' is-disabled' : ''}`}>
      <input
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        type="checkbox"
      />
      <span aria-hidden="true" className="toggle-track">
        <span className="toggle-thumb" />
      </span>
      <span className="toggle-label">{label}</span>
    </label>
  )
}
