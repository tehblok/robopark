type AuthBrandProps = {
  title: string
  subtitle?: string
}

export function AuthBrand({ title, subtitle }: AuthBrandProps) {
  return (
    <div className="auth-brand">
      <span aria-hidden="true" className="brand-mark-grid brand-mark-grid-lg">
        <span />
        <span />
        <span />
        <span />
      </span>
      <div>
        <h1>{title}</h1>
        {subtitle ? <p className="auth-tagline">{subtitle}</p> : null}
      </div>
    </div>
  )
}
