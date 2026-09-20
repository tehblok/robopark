import 'fake-indexeddb/auto'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { openShareTargetInbox, ShareTargetInbox } from './ShareTargetInbox'

describe('ShareTargetInbox', () => {
  it('keeps a shared photo as an unassigned draft until the user attaches it', async () => {
    const inbox = await openShareTargetInbox()
    await inbox.save({ id: 'shared-1', createdAt: 1, name: 'robot.jpg', type: 'image/jpeg',
      blob: new Blob(['photo'], { type: 'image/jpeg' }), assignment: null })
    render(<ShareTargetInbox inbox={inbox} />)

    expect(await screen.findByText('robot.jpg')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Куда прикрепить'), { target: { value: 'task' } })
    fireEvent.change(screen.getByLabelText('Номер задачи'), { target: { value: 'SDCFLEETOPS-1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Прикрепить' }))
    await waitFor(async () => expect((await inbox.list())[0]?.assignment).toEqual({ kind: 'task', target: 'SDCFLEETOPS-1' }))
  })

  it('deletes a shared draft only after explicit cancellation', async () => {
    const inbox = await openShareTargetInbox()
    await inbox.save({ id: 'shared-2', createdAt: 2, name: 'robot.png', type: 'image/png',
      blob: new Blob(['photo'], { type: 'image/png' }), assignment: null })
    render(<ShareTargetInbox inbox={inbox} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Удалить черновик robot.png' }))
    await waitFor(async () => expect((await inbox.list()).some(item => item.id === 'shared-2')).toBe(false))
  })

  it('hands the real blob to the task flow and deletes it only after success', async () => {
    const inbox = await openShareTargetInbox()
    const draft = { id: 'shared-3', createdAt: 3, name: 'robot.jpg', type: 'image/jpeg',
      blob: new Blob(['photo'], { type: 'image/jpeg' }), assignment: null }
    await inbox.save(draft)
    const onAttachTask = vi.fn(async () => undefined)
    render(<ShareTargetInbox inbox={inbox} onAttachTask={onAttachTask} />)
    fireEvent.change(await screen.findByLabelText('Номер задачи'), { target: { value: 'sdcfleetops-1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Прикрепить' }))
    await waitFor(() => expect(onAttachTask).toHaveBeenCalledWith('SDCFLEETOPS-1', expect.objectContaining({ id: 'shared-3', name: 'robot.jpg' })))
    await waitFor(async () => expect((await inbox.list()).some(item => item.id === 'shared-3')).toBe(false))
  })

  it('retains the shared draft when delivery fails', async () => {
    const inbox = await openShareTargetInbox()
    await inbox.save({ id: 'shared-4', createdAt: 4, name: 'robot.jpg', type: 'image/jpeg', blob: new Blob(['photo']), assignment: null })
    render(<ShareTargetInbox inbox={inbox} onAttachTask={async () => { throw new Error('offline') }} />)
    fireEvent.change(await screen.findByLabelText('Номер задачи'), { target: { value: 'SDCFLEETOPS-2' } })
    fireEvent.click(screen.getByRole('button', { name: 'Прикрепить' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Черновик сохранён')
    expect((await inbox.list()).map(item => item.id)).toContain('shared-4')
  })
})
