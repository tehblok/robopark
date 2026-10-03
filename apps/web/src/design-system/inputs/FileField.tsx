import type { InputHTMLAttributes, Ref } from 'react'
import './FileField.css'

type FileFieldProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'value' | 'children'> & {
  label: string
  selectedFileLabel?: string | null
  inputRef?: Ref<HTMLInputElement>
}

export function FileField({ label, selectedFileLabel, inputRef, disabled, ...inputProps }: FileFieldProps) {
  return <label className="rp-file-field" data-disabled={disabled ? 'true' : undefined}>
    <span className="field-label">{label}</span>
    <span className="rp-file-field__control">
      <span aria-hidden="true" className="rp-file-field__choose">Выбрать файл</span>
      <span aria-hidden="true" className="rp-file-field__name" title={selectedFileLabel || undefined}>{selectedFileLabel || 'Файл не выбран'}</span>
      <input {...inputProps} aria-label={label} className="rp-file-field__input" disabled={disabled} ref={inputRef} type="file" />
    </span>
  </label>
}
