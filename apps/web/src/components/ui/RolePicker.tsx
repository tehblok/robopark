import { roleLabel } from '../../i18n/ru'

export const REGISTER_ROLES = ['operator', 'mechanic', 'driver'] as const

export type RegisterRole = (typeof REGISTER_ROLES)[number]

const ROLE_HINT: Record<RegisterRole, string> = {
  operator: 'Мониторинг парка, блокеры и репорты',
  mechanic: 'Задачи на площадке и обращения',
  driver: 'Только проверка робота в Emergency',
}

export function RolePicker({
  value,
  onChange,
}: {
  value: RegisterRole
  onChange: (slug: RegisterRole) => void
}) {
  return (
    <fieldset className="role-picker">
      <legend className="field-label">Роль</legend>
      <div className="role-picker-grid">
        {REGISTER_ROLES.map((slug) => (
          <label className={`role-picker-card${value === slug ? ' is-selected' : ''}`} key={slug}>
            <input
              checked={value === slug}
              name="register-role"
              onChange={() => onChange(slug)}
              type="radio"
              value={slug}
            />
            <strong>{roleLabel(slug)}</strong>
            <span>{ROLE_HINT[slug]}</span>
          </label>
        ))}
      </div>
    </fieldset>
  )
}
