import { useId, useState } from 'react'

export type PasswordFieldProps = {
  label: string
  hint?: string
  value: string
  onChange: (value: string) => void
  autoComplete?: string
  required?: boolean
  minLength?: number
  autoFocus?: boolean
  placeholder?: string
  disabled?: boolean
}

export function PasswordField({
  label,
  hint,
  value,
  onChange,
  autoComplete,
  required,
  minLength,
  autoFocus,
  placeholder,
  disabled,
}: PasswordFieldProps) {
  const [visible, setVisible] = useState(false)
  const inputId = useId()

  return (
    <div className="field">
      <label className="field-label" htmlFor={inputId}>
        {label}
      </label>
      {hint ? <span className="field-hint">{hint}</span> : null}
      <span className="password-field">
        <input
          autoComplete={autoComplete}
          autoFocus={autoFocus}
          disabled={disabled}
          id={inputId}
          minLength={minLength}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
          required={required}
          type={visible ? 'text' : 'password'}
          value={value}
        />
        <button
          aria-label={visible ? 'Скрыть пароль' : 'Показать пароль'}
          className="password-field-toggle"
          disabled={disabled}
          onClick={() => setVisible((current) => !current)}
          type="button"
        >
          {visible ? 'Скрыть' : 'Показать'}
        </button>
      </span>
    </div>
  )
}
