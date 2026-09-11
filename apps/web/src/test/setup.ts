import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

if (typeof window !== 'undefined' && typeof window.matchMedia !== 'function') {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string): MediaQueryList => {
      const listeners = new Set<(event: MediaQueryListEvent) => void>()
      const media = {
        matches: false,
        media: query,
        onchange: null,
        addEventListener: (type: string, listener: EventListenerOrEventListenerObject) => {
          if (type === 'change' && typeof listener === 'function') {
            listeners.add(listener as (event: MediaQueryListEvent) => void)
          }
        },
        removeEventListener: (type: string, listener: EventListenerOrEventListenerObject) => {
          if (type === 'change' && typeof listener === 'function') {
            listeners.delete(listener as (event: MediaQueryListEvent) => void)
          }
        },
        addListener: (listener: (event: MediaQueryListEvent) => void) => listeners.add(listener),
        removeListener: (listener: (event: MediaQueryListEvent) => void) => listeners.delete(listener),
        dispatchEvent: (event: Event) => {
          if (event.type !== 'change') return true
          listeners.forEach((listener) => listener(event as MediaQueryListEvent))
          media.onchange?.(event as MediaQueryListEvent)
          return !event.defaultPrevented
        },
      } as MediaQueryList
      return media
    },
  })
}

afterEach(() => {
  cleanup()
})
