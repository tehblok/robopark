import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import './index.css'
import App from './App.tsx'
import { AuthProvider } from './auth.tsx'
import { GlobalProgress } from './components/GlobalProgress'
import { MaintenanceGate } from './components/ops/MaintenanceGate'
import { ScreenshotGuardGate } from './components/ScreenshotGuard/ScreenshotGuardGate'
import { applyStoredTheme } from './theme'

applyStoredTheme()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <GlobalProgress />
        <MaintenanceGate />
        <ScreenshotGuardGate />
        <App />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
)
