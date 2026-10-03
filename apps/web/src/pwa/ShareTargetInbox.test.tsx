import 'fake-indexeddb/auto'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ShareTargetInbox } from './ShareTargetInbox'
import { clearShareTargetInbox, openShareTargetInbox } from './shareTargetStore'

describe('ShareTargetInbox', () => {
  it('keeps a shared photo as an unassigned draft until the user attaches it', async () => {
    const inbox = await openShareTargetInbox()
    await inbox.save({ id: 'shared-1', createdAt: Date.now() - 4, name: 'robot.jpg', type: 'image/jpeg',
      blob: new Blob(['photo'], { type: 'image/jpeg' }), assignment: null })
    render(<ShareTargetInbox accountId={11} inbox={inbox} shareId="shared-1" />)

    expect(await screen.findByText('robot.jpg')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Куда прикрепить'), { target: { value: 'task' } })
    fireEvent.change(screen.getByLabelText('Номер задачи'), { target: { value: 'SDCFLEETOPS-1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Прикрепить' }))
    await waitFor(async () => expect((await inbox.list(11))[0]?.assignment).toEqual({ kind: 'task', target: 'SDCFLEETOPS-1' }))
  })

  it('deletes a shared draft only after explicit cancellation', async () => {
    const inbox = await openShareTargetInbox()
    await inbox.save({ id: 'shared-2', createdAt: Date.now() - 3, name: 'robot.png', type: 'image/png',
      blob: new Blob(['photo'], { type: 'image/png' }), assignment: null })
    render(<ShareTargetInbox accountId={11} inbox={inbox} shareId="shared-2" />)
    fireEvent.click(await screen.findByRole('button', { name: 'Удалить черновик robot.png' }))
    await waitFor(async () => expect((await inbox.list(11)).some(item => item.id === 'shared-2')).toBe(false))
  })

  it('hands the real blob to the task flow and deletes it only after success', async () => {
    const inbox = await openShareTargetInbox()
    const draft = { id: 'shared-3', createdAt: Date.now() - 2, name: 'robot.jpg', type: 'image/jpeg',
      blob: new Blob(['photo'], { type: 'image/jpeg' }), assignment: null }
    await inbox.save(draft)
    const onAttachTask = vi.fn(async () => undefined)
    render(<ShareTargetInbox accountId={11} inbox={inbox} onAttachTask={onAttachTask} shareId="shared-3" />)
    fireEvent.change(await screen.findByLabelText('Номер задачи'), { target: { value: 'sdcfleetops-1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Прикрепить' }))
    await waitFor(() => expect(onAttachTask).toHaveBeenCalledWith('SDCFLEETOPS-1', expect.objectContaining({ id: 'shared-3', name: 'robot.jpg' })))
    await waitFor(async () => expect((await inbox.list(11)).some(item => item.id === 'shared-3')).toBe(false))
  })

  it('retains the shared draft when delivery fails', async () => {
    const inbox = await openShareTargetInbox()
    await inbox.save({ id: 'shared-4', createdAt: Date.now() - 1, name: 'robot.jpg', type: 'image/jpeg', blob: new Blob(['photo']), assignment: null })
    render(<ShareTargetInbox accountId={11} inbox={inbox} onAttachTask={async () => { throw new Error('offline') }} shareId="shared-4" />)
    fireEvent.change(await screen.findByLabelText('Номер задачи'), { target: { value: 'SDCFLEETOPS-2' } })
    fireEvent.click(screen.getByRole('button', { name: 'Прикрепить' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Черновик сохранён')
    expect((await inbox.list(11)).map(item => item.id)).toContain('shared-4')
  })

  it('refuses to send a claimed photo if the server session switched accounts', async () => {
    const inbox = await openShareTargetInbox()
    await inbox.save({ id: 'account-switch', createdAt: Date.now(), name: 'private.jpg', type: 'image/jpeg',
      blob: new Blob(['photo']), assignment: null })
    const onAttachTask = vi.fn(async () => undefined)
    render(<ShareTargetInbox accountId={11} inbox={inbox} onAttachTask={onAttachTask}
      onVerifyAccount={async () => false} shareId="account-switch" />)
    fireEvent.change(await screen.findByLabelText('Номер задачи'), { target: { value: 'SDCFLEETOPS-1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Прикрепить' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Сессия изменилась')
    expect(onAttachTask).not.toHaveBeenCalled()
  })

  it('hides a photo immediately when another tab changes the login session', async () => {
    const inbox = await openShareTargetInbox()
    await inbox.save({ id: 'other-tab-switch', createdAt: Date.now(), name: 'private-tab.jpg', type: 'image/jpeg',
      blob: new Blob(['photo']), assignment: null })
    render(<ShareTargetInbox accountId={11} inbox={inbox} shareId="other-tab-switch" />)
    expect(await screen.findByText('private-tab.jpg')).toBeInTheDocument()
    window.dispatchEvent(new StorageEvent('storage', { key: 'robopark:auth-transition' }))
    await waitFor(() => expect(screen.queryByText('private-tab.jpg')).not.toBeInTheDocument())
  })

  it('does not reveal a shared photo to another open account', async () => {
    const inbox = await openShareTargetInbox()
    await inbox.clear()
    await inbox.save({ id: 'shared-private', createdAt: Date.now(), name: 'private-robot.jpg', type: 'image/jpeg',
      blob: new Blob(['photo'], { type: 'image/jpeg' }), assignment: null })
    try {
      const first = render(<ShareTargetInbox accountId={11} inbox={inbox} shareId="shared-private" />)
      expect(await screen.findByText('private-robot.jpg')).toBeInTheDocument()
      first.unmount()

      render(<ShareTargetInbox accountId={12} inbox={inbox} shareId="shared-private" />)
      await expect(screen.findByText('private-robot.jpg', {}, { timeout: 100 })).rejects.toThrow()
    } finally {
      await inbox.clear()
      inbox.close?.()
    }
  })

  it('clears only the signed-out account while keeping another account and an unclaimed share', async () => {
    const inbox = await openShareTargetInbox()
    await inbox.clear()
    for (const [id, ownerAccountId] of [['mine', 11], ['other', 12], ['incoming', null]] as const) {
      await inbox.save({ id, ownerAccountId, createdAt: Date.now(), name: `${id}.jpg`, type: 'image/jpeg',
        blob: new Blob(['photo'], { type: 'image/jpeg' }), assignment: null })
    }
    try {
      await clearShareTargetInbox(11)
      expect(await inbox.list(11)).toEqual([])
      expect((await inbox.list(12)).map(item => item.id)).toEqual(['other'])
      expect(await inbox.claim('incoming', 13)).toMatchObject({ id: 'incoming', ownerAccountId: 13 })
    } finally {
      await inbox.clear()
      inbox.close?.()
    }
  })

  it('does not let another account enumerate, assign, or delete an owned draft', async () => {
    const inbox = await openShareTargetInbox()
    await inbox.clear()
    await inbox.save({ id: 'owned-private', ownerAccountId: 11, createdAt: Date.now(), name: 'private.jpg',
      type: 'image/jpeg', blob: new Blob(['photo'], { type: 'image/jpeg' }), assignment: null })
    try {
      expect(await inbox.list(12)).toEqual([])
      await inbox.assign(12, 'owned-private', { kind: 'task', target: 'ROBOPARK-42' })
      await inbox.discard(12, 'owned-private')
      expect(await inbox.list(11)).toMatchObject([{ id: 'owned-private', assignment: null }])
    } finally {
      await inbox.clear()
      inbox.close?.()
    }
  })

  it('bounds incoming photos before claiming even when no inbox was opened earlier', async () => {
    const inbox = await openShareTargetInbox()
    await inbox.clear()
    const now = Date.now()
    for (let index = 0; index < 12; index++) {
      await inbox.save({ id: `incoming-${index}`, ownerAccountId: null, createdAt: now + index,
        name: `robot-${index}.jpg`, type: 'image/jpeg', blob: new Blob(['photo']), assignment: null })
    }
    try {
      expect(await inbox.claim('incoming-11', 11)).toMatchObject({ id: 'incoming-11' })
      expect(await inbox.claim('incoming-0', 11)).toBeNull()
    } finally {
      await inbox.clear()
      inbox.close?.()
    }
  })
})
