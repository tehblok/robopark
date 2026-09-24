export type ThemePreference = 'system' | 'light' | 'dark'
export type ResolvedTheme = 'light' | 'dark'
export type DensityPreference = 'comfortable' | 'compact'
export type ResolvedDensity = DensityPreference

export const THEME_STORAGE_KEY = 'robopark-theme'
export const THEME_MEDIA_QUERY = '(prefers-color-scheme: dark)'
export const DENSITY_STORAGE_KEY = 'robopark-density'
export const DENSITY_MEDIA_QUERY = '(max-width: 899px)'

export function readThemePreference(): ThemePreference {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY)
    return stored === 'light' || stored === 'dark' || stored === 'system' ? stored : 'system'
  } catch {
    return 'system'
  }
}

export function resolveTheme(preference: ThemePreference, systemDark: boolean): ResolvedTheme {
  return preference === 'system' ? (systemDark ? 'dark' : 'light') : preference
}

export function applyTheme(theme: ResolvedTheme): void {
  document.documentElement.dataset.theme = theme
  document.documentElement.style.colorScheme = theme
}

export function readDensityPreference(): DensityPreference {
  try {
    return localStorage.getItem(DENSITY_STORAGE_KEY) === 'compact' ? 'compact' : 'comfortable'
  } catch {
    return 'comfortable'
  }
}

export function resolveDensity(
  preference: DensityPreference,
  _narrowViewport: boolean,
): ResolvedDensity {
  return preference
}

export function applyDensity(density: ResolvedDensity): void {
  document.documentElement.dataset.density = density
}

export function applyInitialTheme(): void {
  const preference = readThemePreference()
  applyTheme(resolveTheme(preference, window.matchMedia(THEME_MEDIA_QUERY).matches))
  const density = readDensityPreference()
  applyDensity(resolveDensity(density, window.matchMedia(DENSITY_MEDIA_QUERY).matches))
}
