import { useEffect, useMemo, useState, type PropsWithChildren } from 'react'
import {
  applyTheme,
  applyDensity,
  applyAccent,
  ACCENT_STORAGE_KEY,
  DENSITY_MEDIA_QUERY,
  DENSITY_STORAGE_KEY,
  readDensityPreference,
  readAccentPreference,
  readThemePreference,
  resolveDensity,
  resolveTheme,
  THEME_MEDIA_QUERY,
  THEME_STORAGE_KEY,
  type DensityPreference,
  type ThemePreference,
  type AccentPreference,
} from './theme'
import { ThemeContext, type ThemeContextValue } from './themeContext'

export type { AccentPreference, DensityPreference, ThemePreference } from './theme'

export function ThemeProvider({ children }: PropsWithChildren) {
  const [preference, setPreferenceState] = useState<ThemePreference>(readThemePreference)
  const [systemDark, setSystemDark] = useState(() => matchMedia(THEME_MEDIA_QUERY).matches)
  const [densityPreference, setDensityPreferenceState] = useState<DensityPreference>(
    readDensityPreference,
  )
  const [accentPreference, setAccentPreferenceState] = useState<AccentPreference>(
    readAccentPreference,
  )
  const [narrowViewport, setNarrowViewport] = useState(
    () => matchMedia(DENSITY_MEDIA_QUERY).matches,
  )
  const resolvedTheme = resolveTheme(preference, systemDark)
  const resolvedDensity = resolveDensity(densityPreference, narrowViewport)

  useEffect(() => {
    const media = matchMedia(THEME_MEDIA_QUERY)
    const onChange = (event: MediaQueryListEvent) => setSystemDark(event.matches)
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])

  useEffect(() => {
    const media = matchMedia(DENSITY_MEDIA_QUERY)
    const onChange = (event: MediaQueryListEvent) => setNarrowViewport(event.matches)
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])

  useEffect(() => applyTheme(resolvedTheme), [resolvedTheme])
  useEffect(() => applyDensity(resolvedDensity), [resolvedDensity])
  useEffect(() => applyAccent(accentPreference), [accentPreference])

  const value = useMemo<ThemeContextValue>(() => ({
    preference,
    resolvedTheme,
    densityPreference,
    resolvedDensity,
    accentPreference,
    setPreference(next) {
      try {
        localStorage.setItem(THEME_STORAGE_KEY, next)
      } catch {
        // The in-memory preference remains available when storage is denied.
      }
      setPreferenceState(next)
    },
    setDensityPreference(next) {
      try {
        localStorage.setItem(DENSITY_STORAGE_KEY, next)
      } catch {
        // The in-memory preference remains available when storage is denied.
      }
      setDensityPreferenceState(next)
    },
    setAccentPreference(next) {
      try {
        localStorage.setItem(ACCENT_STORAGE_KEY, next)
      } catch {
        // The in-memory preference remains available when storage is denied.
      }
      setAccentPreferenceState(next)
    },
  }), [accentPreference, densityPreference, preference, resolvedDensity, resolvedTheme])

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}
