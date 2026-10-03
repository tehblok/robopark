import { createContext, useContext } from 'react'
import type {
  AccentPreference,
  DensityPreference,
  ResolvedDensity,
  ResolvedTheme,
  ThemePreference,
} from './theme'

export type ThemeContextValue = {
  preference: ThemePreference
  resolvedTheme: ResolvedTheme
  setPreference: (preference: ThemePreference) => void
  densityPreference: DensityPreference
  resolvedDensity: ResolvedDensity
  setDensityPreference: (preference: DensityPreference) => void
  accentPreference: AccentPreference
  setAccentPreference: (preference: AccentPreference) => void
}

export const ThemeContext = createContext<ThemeContextValue | null>(null)

export function useTheme(): ThemeContextValue {
  const value = useContext(ThemeContext)
  if (!value) throw new Error('useTheme must be used inside ThemeProvider')
  return value
}
