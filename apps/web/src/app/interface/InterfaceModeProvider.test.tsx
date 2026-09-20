import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useState } from 'react'
import { InterfaceModeProvider } from './InterfaceModeProvider'
import { usePresentationMode } from './presentationModeContext'
import { InterfaceChoice } from './InterfaceChoice'
import { createInterfaceModeStore } from './interfaceModeStore'
import { PresentationShell } from './PresentationShell'
import * as styles from './loadInterfaceStyles'

describe('interface preference UI', () => {
  it('does not offer an account-scoped choice before authentication', () => {
    const store = createInterfaceModeStore(() => localStorage)
    render(<InterfaceModeProvider accountId={null} store={store}><InterfaceChoice /></InterfaceModeProvider>)

    expect(screen.queryByRole('radiogroup', { name: 'Интерфейс' })).not.toBeInTheDocument()
    expect(document.documentElement).not.toHaveAttribute('data-interface')
  })
  it('keeps authenticated presentation neutral until a persisted A mode is ready', async () => {
    let finish!: () => void
    const load = vi.spyOn(styles, 'loadInterfaceStyles').mockImplementationOnce(
      () => new Promise(resolve => { finish = () => resolve({ default: '' }) }),
    )
    localStorage.setItem('robopark:interface:v1:810', 'task-first')
    const store = createInterfaceModeStore(() => localStorage)
    function Probe() {
      return <span>{usePresentationMode() ?? 'neutral'}</span>
    }

    render(<InterfaceModeProvider accountId={810} store={store}><Probe /></InterfaceModeProvider>)
    expect(screen.getByText('neutral')).toBeVisible()
    expect(document.documentElement).not.toHaveAttribute('data-interface')
    finish()
    await waitFor(() => expect(screen.getByText('task-first')).toBeVisible())
    expect(document.documentElement.dataset.interface).toBe('task-first')
    load.mockRestore()
  })
  it('returns to classic when the new stylesheet fails without removing the draft', async () => {
    const load = vi.spyOn(styles, 'loadInterfaceStyles').mockRejectedValueOnce(new Error('offline'))
    const store = createInterfaceModeStore(() => localStorage)
    function ShellProbe() {
      const mode = usePresentationMode()
      return <>{mode ? <PresentationShell mode={mode} slots={{
        navigation: <span>Навигация</span>, header: <span>Заголовок</span>,
        content: <input aria-label="Черновик" defaultValue="Сохранить текст" />,
        context: <span>Контекст</span>, action: <button type="button">Действие</button>,
      }} /> : null}</>
    }
    render(<InterfaceModeProvider accountId={809} store={store}><InterfaceChoice /><ShellProbe /></InterfaceModeProvider>)
    fireEvent.click(screen.getByRole('radio', { name: 'Новый А' }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Классический'))
    expect(document.documentElement.dataset.interface).toBe('classic')
    expect(screen.getByTestId('classic-shell')).toBeInTheDocument()
    expect(screen.queryByTestId('task-first-shell')).not.toBeInTheDocument()
    expect(localStorage.getItem('robopark:interface:v1:809')).toBe('classic')
    expect(screen.getByRole('textbox', { name: 'Черновик' })).toHaveValue('Сохранить текст')
    load.mockRestore()
  })

  it('loads interface A once across repeated presentation switches', async () => {
    const load = vi.spyOn(styles, 'loadInterfaceStyles').mockResolvedValue({ default: '' })
    const store = createInterfaceModeStore(() => localStorage)
    render(<InterfaceModeProvider accountId={811} store={store}><InterfaceChoice /></InterfaceModeProvider>)

    fireEvent.click(screen.getByRole('radio', { name: 'Новый А' }))
    await waitFor(() => expect(document.documentElement.dataset.interface).toBe('task-first'))
    fireEvent.click(screen.getByRole('radio', { name: 'Классический' }))
    fireEvent.click(screen.getByRole('radio', { name: 'Новый А' }))
    await waitFor(() => expect(document.documentElement.dataset.interface).toBe('task-first'))
    expect(load).toHaveBeenCalledTimes(1)
    load.mockRestore()
  })
  it('switches without remounting forms or losing selected files and defers during a write', async () => {
    const store = createInterfaceModeStore(() => localStorage)
    function Draft() {
      const [text, setText] = useState('')
      return <><input aria-label="Комментарий" value={text} onChange={event => setText(event.target.value)} /><input aria-label="Фото" type="file" /></>
    }
    render(<InterfaceModeProvider accountId={808} store={store}><InterfaceChoice /><Draft /></InterfaceModeProvider>)
    const comment = screen.getByRole('textbox', { name: 'Комментарий' })
    const photo = screen.getByLabelText('Фото') as HTMLInputElement
    const file = new File(['photo'], 'repair.jpg', { type: 'image/jpeg' })
    fireEvent.change(comment, { target: { value: 'Колесо заменено' } })
    fireEvent.change(photo, { target: { files: [file] } })
    let release!: () => void
    act(() => { release = store.beginMutation() })
    fireEvent.click(screen.getByRole('radio', { name: 'Новый А' }))
    expect(screen.getByRole('status')).toHaveTextContent('после завершения операции')
    expect(document.documentElement.dataset.interface).toBe('classic')
    act(() => release())
    await waitFor(() => expect(document.documentElement.dataset.interface).toBe('task-first'))
    expect(screen.getByRole('textbox', { name: 'Комментарий' })).toBe(comment)
    expect(comment).toHaveValue('Колесо заменено')
    expect(photo.files?.[0]).toBe(file)
    fireEvent.click(screen.getByRole('radio', { name: 'Классический' }))
    expect(document.documentElement.dataset.interface).toBe('classic')
  })
})
