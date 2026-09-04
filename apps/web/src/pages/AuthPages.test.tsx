import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthContext, type AuthContextValue } from '../auth-context'
import { ThemeProvider } from '../design-system/theme/ThemeProvider'
import { installMatchMedia } from '../test/renderApp'
import { Login } from './Login'
import { Register } from './Register'

function renderAuthPage(page: React.ReactNode, overrides: Partial<AuthContextValue> = {}) {
  const auth: AuthContextValue = {
    user: null,
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
    refreshUser: vi.fn(),
    ...overrides,
  }

  return render(
    <MemoryRouter>
      <ThemeProvider>
        <AuthContext.Provider value={auth}>{page}</AuthContext.Provider>
      </ThemeProvider>
    </MemoryRouter>,
  )
}

describe('authentication pages', () => {
  beforeEach(() => {
    localStorage.clear()
    installMatchMedia({ width: 1440 })
    document.documentElement.dataset.theme = 'light'
  })

  it('gives unauthenticated users the product context and a working theme choice', async () => {
    const user = userEvent.setup()
    renderAuthPage(<Login />)

    expect(screen.getByRole('heading', { name: 'Вход в Робопарк' })).toBeVisible()
    expect(screen.getByRole('img', { name: 'Робопарк Сервис' })).toBeVisible()
    expect(screen.getByRole('region', { name: 'Управление парком роботов' })).toBeVisible()
    expect(screen.getByRole('img', { name: 'Робот-доставщик' })).toBeVisible()
    expect(screen.getByRole('textbox', { name: 'Логин' })).toHaveAttribute('autocomplete', 'username')
    expect(screen.getByLabelText('Пароль')).toHaveAttribute('autocomplete', 'current-password')

    await user.click(screen.getByRole('button', { name: 'Тёмная тема' }))

    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(localStorage.getItem('robopark-theme')).toBe('dark')
    expect(screen.getByRole('button', { name: 'Тёмная тема' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('uses the same product shell for registration and explains the approval flow', () => {
    renderAuthPage(<Register />)

    expect(screen.getByRole('heading', { name: 'Создание аккаунта' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Вся смена — в одном понятном контуре' })).not.toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Управление парком роботов' })).toBeVisible()
    expect(screen.getByText('Доступ активирует владелец')).toBeVisible()
    expect(screen.getByText('Проверка робота и задачи перемещения')).toBeVisible()
    expect(screen.getByLabelText('Общий пароль')).not.toHaveFocus()
    expect(screen.getByRole('link', { name: 'Войти' })).toHaveAttribute('href', '/login')
  })
})
