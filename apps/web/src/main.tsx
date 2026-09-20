import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import './index.css'
import './design-system/styles/index.css'
import './app/interface/classic-robot-check.css'
import './app/interface/interfaceTokens.css'
import App from './App.tsx'
import { AuthProvider } from './auth.tsx'
import { GlobalProgress } from './components/GlobalProgress'
import { MaintenanceGate } from './components/ops/MaintenanceGate'
import { ScreenshotGuardGate } from './components/ScreenshotGuard/ScreenshotGuardGate'
import { ThemeProvider } from './design-system/theme/ThemeProvider'
import { applyInitialTheme } from './design-system/theme/theme'
import { registerServiceWorker } from './pwa/registerServiceWorker'

applyInitialTheme()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider>
      <BrowserRouter>
        <AuthProvider>
          <GlobalProgress />
          <MaintenanceGate />
          <ScreenshotGuardGate />
          <App />
        </AuthProvider>
      </BrowserRouter>
    </ThemeProvider>
  </StrictMode>,
)

if (import.meta.env.PROD) {
  window.addEventListener('load', () => {
    void registerServiceWorker({
      production: true,
      secure: window.isSecureContext,
      serviceWorker: navigator.serviceWorker,
      onFocus: (callback) => window.addEventListener('focus', callback),
      onVisible: (callback) => document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') callback()
      }),
    })
  }, { once: true })
}
