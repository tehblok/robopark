import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { TerminalPage } from './domains/system/terminal/TerminalPage'
import { isDesktopLoopbackOrigin } from './pwa/desktopRuntime'
import './design-system/styles/index.css'

createRoot(document.getElementById('terminal-root')!).render(<StrictMode>{isDesktopLoopbackOrigin()
  ? <main className="rp-page"><h1>Терминал недоступен в приложении</h1><p>Откройте серверный сайт при подключённой сети.</p></main>
  : <TerminalPage />}</StrictMode>)
