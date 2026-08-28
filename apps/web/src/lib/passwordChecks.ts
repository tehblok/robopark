export type PasswordChecks = {
  length: boolean
  lower: boolean
  upper: boolean
  digit: boolean
  special: boolean
  classes: number
  ok: boolean
}

export function passwordChecks(password: string): PasswordChecks {
  const lower = /[a-z]/.test(password)
  const upper = /[A-Z]/.test(password)
  const digit = /\d/.test(password)
  const special = /[^A-Za-z0-9]/.test(password)
  const length = password.length >= 12
  const classes = [lower, upper, digit, special].filter(Boolean).length
  return {
    length,
    lower,
    upper,
    digit,
    special,
    classes,
    ok: length && classes >= 3,
  }
}
