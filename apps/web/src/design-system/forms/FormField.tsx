import { cloneElement, type ReactElement, type ReactNode } from 'react'
import './FormField.css'

type FormControlProps = {
  id?: string
  required?: boolean
  'aria-invalid'?: boolean
  'aria-describedby'?: string
}

export type FormFieldProps = {
  id: string
  label: ReactNode
  hint?: ReactNode
  error?: ReactNode
  required?: boolean
  children: ReactElement
  className?: string
}

export function FormField({
  id,
  label,
  hint,
  error,
  required = false,
  children,
  className = '',
}: FormFieldProps): ReactElement {
  const control = children as ReactElement<FormControlProps>
  const callerTokens = control.props['aria-describedby']?.trim().split(/\s+/).filter(Boolean) ?? []
  const describedBy = [
    ...callerTokens,
    ...(hint ? [`${id}-hint`] : []),
    ...(error ? [`${id}-error`] : []),
  ].join(' ') || undefined

  return (
    <div className={`rp-form-field ${className}`.trim()}>
      <label className="rp-form-field__label" htmlFor={id}>
        {label}
      </label>
      {cloneElement(control, {
        id,
        required,
        'aria-invalid': Boolean(error),
        'aria-describedby': describedBy,
      })}
      {hint ? <div className="rp-form-field__hint" id={`${id}-hint`}>{hint}</div> : null}
      {error ? <div className="rp-form-field__error" id={`${id}-error`} role="alert">{error}</div> : null}
    </div>
  )
}
