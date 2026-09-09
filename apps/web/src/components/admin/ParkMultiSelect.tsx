import { useEffect, useId, useRef, useState } from 'react'
import type { Park } from '../../api'

type ParkMultiSelectProps = {
  parks: Park[]
  value: number[]
  onChange(next: number[]): void
  disabled?: boolean
  label: string
}

function uniqueSorted(ids: number[]): number[] {
  return [...new Set(ids)].sort((left, right) => left - right)
}

export function ParkMultiSelect({ parks, value, onChange, disabled = false, label }: ParkMultiSelectProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const root = useRef<HTMLDivElement>(null)
  const filterInput = useRef<HTMLInputElement>(null)
  const listboxId = useId()
  const selected = new Set(value)
  const activeParks = parks.filter((park) => park.is_active !== false)
  const visibleParks = parks.filter((park) => {
    const needle = query.trim().toLocaleLowerCase('ru')
    return !needle || `${park.name} ${park.tag}`.toLocaleLowerCase('ru').includes(needle)
  })

  useEffect(() => {
    if (!open) return
    filterInput.current?.focus()
    const closeOnOutsidePointer = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', closeOnOutsidePointer)
    return () => document.removeEventListener('mousedown', closeOnOutsidePointer)
  }, [open])

  const update = (next: number[]) => onChange(uniqueSorted(next))
  const toggle = (park: Park) => {
    if (disabled || (park.is_active === false && !selected.has(park.id))) return
    update(selected.has(park.id) ? value.filter((id) => id !== park.id) : [...value, park.id])
  }

  return <div className="park-multi-select" ref={root}>
    <button
      aria-controls={open ? listboxId : undefined}
      aria-expanded={open}
      aria-haspopup="dialog"
      className="park-multi-select__trigger"
      disabled={disabled}
      onClick={() => setOpen((current) => !current)}
      type="button"
    >
      {label}. Выбрано: {value.length}
    </button>
    {open ? <div
      aria-label={label}
      aria-modal="false"
      className="park-multi-select__menu"
      id={listboxId}
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          event.preventDefault()
          setOpen(false)
        }
      }}
      role="dialog"
    >
      <div className="park-multi-select__tools">
        <input
          aria-label="Поиск парков"
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Поиск"
          ref={filterInput}
          value={query}
        />
        <button onClick={() => update([...value, ...activeParks.map((park) => park.id)])} type="button">Выбрать все</button>
        <button onClick={() => update([])} type="button">Очистить</button>
      </div>
      <div className="park-multi-select__options">
        {visibleParks.map((park) => {
          const isSelected = selected.has(park.id)
          const unavailable = park.is_active === false && !isSelected
          const optionLabel = `${park.name}${park.is_active === false ? ' · неактивен' : ''}`
          return <label className="admin-perm-check" key={park.id}>
            <input
              aria-label={optionLabel}
              checked={isSelected}
              disabled={disabled || unavailable}
              onChange={() => toggle(park)}
              type="checkbox"
            />
            {optionLabel}
          </label>
        })}
        {visibleParks.length === 0 ? <p className="issue-muted">Парки не найдены</p> : null}
      </div>
    </div> : null}
  </div>
}
