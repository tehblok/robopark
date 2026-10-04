import { useId, useState } from 'react'
import type { RepairComponent } from '../../api'

/** Keeps every current value, including retired choices, until the person removes it. */
export function RepairComponentPicker({ options, value, onChange }: {
  options: readonly RepairComponent[]
  value: readonly string[]
  onChange: (ids: string[]) => void
}) {
  const [query, setQuery] = useState('')
  const id = useId()
  const search = query.trim().toLocaleLowerCase('ru').replaceAll('ё', 'е')
  const known = new Set(options.map(item => item.id))
  const choices: RepairComponent[] = [...options, ...value.filter(item => !known.has(item)).map(item => ({ id: item, label: 'Текущая деталь' }))]
  const filtered = choices.filter(item => !search || [item.label, item.tracker_name, ...(item.aliases ?? [])].join(' ').toLocaleLowerCase('ru').replaceAll('ё', 'е').includes(search))
  return <div className="rp-repair-component-picker">
    <label className="field" htmlFor={id}><span>Найти деталь или узел</span>
      <input autoComplete="off" id={id} onChange={event => setQuery(event.target.value)} placeholder="Например, колесо" type="search" value={query} />
    </label>
    <div aria-label="Компоненты ремонта" className="rp-repair-component-choices" role="group">
      {filtered.map(item => <label className="rp-repair-component-choice" key={item.id}>
        <input checked={value.includes(item.id)} onChange={event => onChange(event.target.checked ? [...value, item.id] : value.filter(entry => entry !== item.id))} type="checkbox" />
        <span>{item.label}</span>
      </label>)}
      {!filtered.length ? <p role="status">Ничего не найдено. Попробуйте другое название.</p> : null}
    </div>
    {value.length ? <p className="muted">Выбрано: {choices.filter(item => value.includes(item.id)).map(item => item.label).join(', ')}</p> : null}
  </div>
}
