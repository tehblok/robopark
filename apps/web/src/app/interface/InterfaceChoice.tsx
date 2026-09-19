import { useId } from 'react'
import { useInterfaceMode } from './InterfaceModeProvider'
import './interface-choice.css'

export function InterfaceChoice() {
  const { mode, pendingMode, requestMode } = useInterfaceMode()
  const name = useId()
  return <fieldset className="rp-shell__preference-group rp-interface-choice" role="radiogroup" aria-label="Интерфейс">
    <legend>Интерфейс</legend>
    {([['classic', 'Классический'], ['task-first', 'Новый А']] as const).map(([value, label]) => (
      <label key={value}><input type="radio" name={name} value={value}
        checked={(pendingMode ?? mode) === value} onChange={() => requestMode(value)} />{label}</label>
    ))}
    {pendingMode ? <p role="status">Переключим после завершения операции</p> : null}
  </fieldset>
}
