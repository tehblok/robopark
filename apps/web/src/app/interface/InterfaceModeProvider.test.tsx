import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useState } from 'react'
import { InterfaceModeProvider } from './InterfaceModeProvider'
import { InterfaceChoice } from './InterfaceChoice'
import { createInterfaceModeStore } from './interfaceModeStore'
import * as styles from './loadInterfaceStyles'

describe('interface preference UI', () => {
  it('returns to classic when the new stylesheet fails without removing the draft', async () => {
    const load = vi.spyOn(styles, 'loadInterfaceStyles').mockRejectedValueOnce(new Error('offline'))
    const store = createInterfaceModeStore(() => localStorage)
    render(<InterfaceModeProvider accountId={809} store={store}><InterfaceChoice /><input aria-label="Черновик" defaultValue="Сохранить текст" /></InterfaceModeProvider>)
    fireEvent.click(screen.getByRole('radio', { name: 'Новый А' }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Классический'))
    expect(document.documentElement.dataset.interface).toBe('classic')
    expect(screen.getByRole('textbox', { name: 'Черновик' })).toHaveValue('Сохранить текст')
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
