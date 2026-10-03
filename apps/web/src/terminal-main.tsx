import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { TerminalPage } from './domains/system/terminal/TerminalPage'
import './design-system/styles/index.css'

createRoot(document.getElementById('terminal-root')!).render(<StrictMode><TerminalPage /></StrictMode>)
