import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { InterfaceModeProvider } from './InterfaceModeProvider'
import { usePresentationMode } from './presentationModeContext'
import { createInterfaceModeStore } from './interfaceModeStore'
import { PresentationShell } from './PresentationShell'

describe('classic interface provider', () => {
  it('keeps presentation neutral before authentication', () => {
    const store = createInterfaceModeStore(() => localStorage)
    function Probe() { return <span>{usePresentationMode() ?? 'neutral'}</span> }

    render(<InterfaceModeProvider accountId={null} store={store}><Probe /></InterfaceModeProvider>)

    expect(screen.getByText('neutral')).toBeVisible()
    expect(document.documentElement).not.toHaveAttribute('data-interface')
  })

  it('migrates legacy storage without rendering the removed shell', () => {
    localStorage.setItem('robopark:interface:v1:810', ['task', 'first'].join('-'))
    const store = createInterfaceModeStore(() => localStorage)
    function ShellProbe() {
      const mode = usePresentationMode()
      return mode ? <PresentationShell mode={mode} slots={{
        navigation: <span>Навигация</span>,
        header: <span>Заголовок</span>,
        content: <span>Содержимое</span>,
      }} /> : null
    }

    render(<InterfaceModeProvider accountId={810} store={store}><ShellProbe /></InterfaceModeProvider>)

    expect(document.documentElement.dataset.interface).toBe('classic')
    expect(localStorage.getItem('robopark:interface:v1:810')).toBeNull()
    expect(screen.getByTestId('classic-shell')).toBeInTheDocument()
  })
})
