import type { PropsWithChildren } from 'react'
import robotImage from '../../assets/robots/isometric.png'
import { useTheme, type ThemePreference } from '../../design-system/theme/ThemeProvider'
import './auth.css'

const THEME_OPTIONS: readonly { value: ThemePreference; label: string; shortLabel: string }[] = [
  { value: 'system', label: 'Системная тема', shortLabel: 'Авто' },
  { value: 'light', label: 'Светлая тема', shortLabel: 'Светлая' },
  { value: 'dark', label: 'Тёмная тема', shortLabel: 'Тёмная' },
]

function AuthThemePicker() {
  const { preference, setPreference } = useTheme()

  return (
    <div aria-label="Тема оформления" className="rp-auth__themes" role="group">
      {THEME_OPTIONS.map((option) => (
        <button
          aria-label={option.label}
          aria-pressed={preference === option.value}
          className={preference === option.value ? 'is-active' : ''}
          key={option.value}
          onClick={() => setPreference(option.value)}
          type="button"
        >
          {option.shortLabel}
        </button>
      ))}
    </div>
  )
}

export function AuthLayout({ children }: PropsWithChildren) {
  return (
    <main className="rp-auth">
      <header className="rp-auth__topbar">
        <div className="rp-auth__brand">РобоПарк</div>
        <AuthThemePicker />
      </header>

      <div className="rp-auth__stage">
        <section aria-label="Управление парком роботов" className="rp-auth__story">
          <div className="rp-auth__story-copy">
            <span className="rp-auth__eyebrow">Рабочее пространство команды</span>
            <p className="rp-auth__story-title">Вся смена — в одном понятном контуре</p>
            <p className="rp-auth__story-description">
              Очередь задач, состояние роботов, SLA и коммуникация команды без лишних переходов.
            </p>
            <ul aria-label="Возможности платформы" className="rp-auth__capabilities">
              <li>Очередь по приоритету</li>
              <li>Проверка робота</li>
              <li>Контроль парка</li>
            </ul>
          </div>
          <div className="rp-auth__robot-wrap">
            <div aria-hidden="true" className="rp-auth__orbit" />
            <img alt="Робот-доставщик" className="rp-auth__robot" src={robotImage} />
            <div className="rp-auth__robot-note">
              <span aria-hidden="true" />
              Задачи · Роботы · Репорты
            </div>
          </div>
        </section>

        <section className="rp-auth__content">{children}</section>
      </div>

      <footer className="rp-auth__footer">
        <span>Внутренняя система управления парком</span>
        <span>Защищённый доступ</span>
      </footer>
    </main>
  )
}
