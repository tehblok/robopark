/* eslint-disable react/only-export-components */
import { render } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { vi } from 'vitest'
import { api, type User } from '../api'
import { AuthContext } from '../auth-context'
import { AppRouter } from '../app/routing/AppRouter'
import { InterfaceModeProvider } from '../app/interface/InterfaceModeProvider'
import { ThemeProvider } from '../design-system/theme/ThemeProvider'

type MutableMediaQuery = MediaQueryList & {
  setMatches: (matches: boolean) => void
}

export type MatchMediaController = {
  setSystemDark: (matches: boolean) => void
  setWidth: (width: number) => void
}

export function installMatchMedia({
  systemDark = false,
  width = 1200,
}: { systemDark?: boolean; width?: number } = {}): MatchMediaController {
  const queries = new Map<string, MutableMediaQuery>()

  const matchesQuery = (query: string) => {
    if (query === '(prefers-color-scheme: dark)') return systemDark
    if (query === '(max-width: 899px)') return width <= 899
    if (query === '(min-width: 900px) and (max-width: 1199px)') {
      return width >= 900 && width <= 1199
    }
    if (query === '(min-width: 1200px)') return width >= 1200
    return false
  }

  const mediaFor = (query: string): MutableMediaQuery => {
    const existing = queries.get(query)
    if (existing) return existing

    const queryListeners = new Set<(event: MediaQueryListEvent) => void>()
    const media = {
      media: query,
      matches: matchesQuery(query),
      onchange: null,
      addEventListener: (_type: string, listener: EventListenerOrEventListenerObject) => {
        queryListeners.add(listener as (event: MediaQueryListEvent) => void)
      },
      removeEventListener: (_type: string, listener: EventListenerOrEventListenerObject) => {
        queryListeners.delete(listener as (event: MediaQueryListEvent) => void)
      },
      addListener: (listener: (event: MediaQueryListEvent) => void) => {
        queryListeners.add(listener)
      },
      removeListener: (listener: (event: MediaQueryListEvent) => void) => {
        queryListeners.delete(listener)
      },
      dispatchEvent: () => true,
      setMatches(matches: boolean) {
        if (media.matches === matches) return
        Object.defineProperty(media, 'matches', { configurable: true, value: matches })
        const event = { matches, media: query } as MediaQueryListEvent
        queryListeners.forEach((listener) => listener(event))
        media.onchange?.(event)
      },
    } as MutableMediaQuery
    queries.set(query, media)
    return media
  }

  vi.stubGlobal('matchMedia', vi.fn((query: string) => mediaFor(query)))

  const updateQueries = () => {
    for (const [query, media] of queries) media.setMatches(matchesQuery(query))
  }

  return {
    setSystemDark(matches) {
      systemDark = matches
      updateQueries()
    },
    setWidth(nextWidth) {
      width = nextWidth
      updateQueries()
    },
  }
}

export function testUser(overrides: Partial<User> = {}): User {
  return {
    id: 1,
    username: 'test-user',
    role: 'operator',
    access_status: 'approved',
    permissions: [],
    parks: [],
    ...overrides,
  }
}

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}{location.search}</output>
}

export function renderApp(
  path: string,
  user: User | null,
  { loading = false }: { loading?: boolean } = {},
) {
  if (!vi.isMockFunction(api.reportsBadge)) {
    vi.spyOn(api, 'reportsBadge').mockResolvedValue({ count: 0 })
  }

  const tree = (currentUser: User | null, authLoading: boolean) => (
    <MemoryRouter initialEntries={[path]}>
      <ThemeProvider>
        <AuthContext.Provider value={{
          user: currentUser,
          loading: authLoading,
          login: vi.fn(),
          refreshUser: vi.fn(),
          logout: vi.fn(),
        }}>
          <InterfaceModeProvider accountId={currentUser?.id ?? null}>
            <AppRouter />
            <LocationProbe />
          </InterfaceModeProvider>
        </AuthContext.Provider>
      </ThemeProvider>
    </MemoryRouter>
  )

  const result = render(tree(user, loading))
  return {
    ...result,
    rerenderAuth(nextUser: User | null, nextLoading = false) {
      result.rerender(tree(nextUser, nextLoading))
    },
  }
}
