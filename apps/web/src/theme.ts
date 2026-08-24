export type Theme = 'light' | 'dark'

export const THEME_STORAGE_KEY = 'robopark-theme'

export function getStoredTheme(): Theme {
  const raw = localStorage.getItem(THEME_STORAGE_KEY)
  return raw === 'dark' ? 'dark' : 'light'
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme
}

export function setTheme(theme: Theme): void {
  localStorage.setItem(THEME_STORAGE_KEY, theme)
  applyTheme(theme)
}

export function applyStoredTheme(): void {
  applyTheme(getStoredTheme())
}
