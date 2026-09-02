import { createContext, useContext, useEffect, useMemo, useState, type PropsWithChildren } from 'react'
import {
  applyTheme,
  applyDensity,
  DENSITY_MEDIA_QUERY,
  DENSITY_STORAGE_KEY,
  readDensityPreference,
  readThemePreference,
  resolveDensity,
  resolveTheme,
  THEME_MEDIA_QUERY,
  THEME_STORAGE_KEY,
  type ResolvedTheme,
  type ResolvedDensity,
  type DensityPreference,
  type ThemePreference,
} from './theme'

export type { DensityPreference, ThemePreference } from './theme'

type ThemeContextValue = {
  preference: ThemePreference
  resolvedTheme: ResolvedTheme
  setPreference: (preference: ThemePreference) => void
  densityPreference: DensityPreference
  resolvedDensity: ResolvedDensity
  setDensityPreference: (preference: DensityPreference) => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

export function ThemeProvider({ children }: PropsWithChildren) {
  const [preference, setPreferenceState] = useState<ThemePreference>(readThemePreference)
  const [systemDark, setSystemDark] = useState(() => matchMedia(THEME_MEDIA_QUERY).matches)
  const [densityPreference, setDensityPreferenceState] = useState<DensityPreference>(
    readDensityPreference,
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

  const value = useMemo<ThemeContextValue>(() => ({
    preference,
    resolvedTheme,
    densityPreference,
    resolvedDensity,
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
  }), [densityPreference, preference, resolvedDensity, resolvedTheme])

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme(): ThemeContextValue {
  const value = useContext(ThemeContext)
  if (!value) throw new Error('useTheme must be used inside ThemeProvider')
  return value
}
