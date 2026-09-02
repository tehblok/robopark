import { forwardRef, type ButtonHTMLAttributes } from 'react'
import { Icon, type IconName } from '../icons/Icon'
import './Button.css'

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  size?: 'comfortable' | 'compact'
  leadingIcon?: IconName
  busy?: boolean
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'primary',
    size = 'comfortable',
    leadingIcon,
    busy = false,
    disabled,
    className = '',
    children,
    ...props
  },
  ref,
) {
  return (
    <button
      {...props}
      aria-busy={busy || undefined}
      className={`rp-button rp-button--${variant} rp-button--${size} ${className}`.trim()}
      disabled={disabled || busy}
      ref={ref}
    >
      {leadingIcon ? <Icon name={leadingIcon} size={18} /> : null}
      <span>{children}</span>
    </button>
  )
})

export function IconButton({
  label,
  icon,
  ...props
}: Omit<ButtonProps, 'children' | 'leadingIcon'> & { label: string; icon: IconName }) {
  return (
    <Button {...props} aria-label={label} className="rp-icon-button">
      <Icon name={icon} />
    </Button>
  )
}
