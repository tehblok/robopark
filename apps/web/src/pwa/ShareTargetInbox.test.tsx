import 'fake-indexeddb/auto'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ShareTargetInbox } from './ShareTargetInbox'
import { clearShareTargetInbox, openShareTargetInbox } from './shareTargetStore'
import { resetDesktopStorageKeyForTests, setDesktopRuntimeForTests } from './desktopRuntime'

async function rawShareDraft(id: string): Promise<Record<string, unknown>> {
  const database = await new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open('robopark-share-inbox-v2', 1)
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
  try {
    return await new Promise<Record<string, unknown>>((resolve, reject) => {
      const request = database.transaction('drafts').objectStore('drafts').get(id)
      request.onsuccess = () => resolve(request.result)
      request.onerror = () => reject(request.error)
    })
  } finally { database.close() }
}

async function replaceRawShareDraft(record: Record<string, unknown>): Promise<void> {
  const database = await new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open('robopark-share-inbox-v2', 1)
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
  try {
    const transaction = database.transaction('drafts', 'readwrite')
    transaction.objectStore('drafts').put(record)
    await new Promise<void>((resolve, reject) => {
      transaction.oncomplete = () => resolve()
      transaction.onabort = transaction.onerror = () => reject(transaction.error)
    })
  } finally { database.close() }
}

describe('ShareTargetInbox', () => {
  it('encrypts shared photo metadata and bytes in the desktop database', async () => {
    setDesktopRuntimeForTests({ version: 1, instanceId: '6f17d720-9fab-48da-8dd5-9f3d270b2878' })
    const key = crypto.getRandomValues(new Uint8Array(32))
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ key: btoa(String.fromCharCode(...key)) }), { status: 200 })))
    const inbox = await openShareTargetInbox()
    try {
      await inbox.clear()
      await inbox.save({ id: 'desktop-encrypted', ownerAccountId: 11, createdAt: Date.now(), name: 'private-robot.jpg',
        type: 'image/jpeg', blob: new Blob(['photo']), assignment: null })
      const database = await new Promise<IDBDatabase>((resolve, reject) => {
        const request = indexedDB.open('robopark-share-inbox-v2', 1)
        request.onsuccess = () => resolve(request.result)
        request.onerror = () => reject(request.error)
      })
      const record = await new Promise<unknown>((resolve, reject) => {
        const request = database.transaction('drafts').objectStore('drafts').get('desktop-encrypted')
        request.onsuccess = () => resolve(request.result)
        request.onerror = () => reject(request.error)
      })
      database.close()
      expect(record).not.toHaveProperty('name')
      expect(record).not.toHaveProperty('type')
      expect(record).not.toHaveProperty('blob')
      expect(record).not.toHaveProperty('assignment')
      expect(JSON.stringify(record)).not.toContain('private-robot.jpg')
      expect(JSON.stringify(record)).not.toContain('cGhvdG8=')
      expect(await inbox.list(11)).toMatchObject([{ name: 'private-robot.jpg' }])
    } finally {
      await inbox.clear(); inbox.close?.(); setDesktopRuntimeForTests(undefined); resetDesktopStorageKeyForTests(); vi.unstubAllGlobals()
    }
  })
  it('rejects tampered desktop routing fields and retains ciphertext while its key is unavailable', async () => {
    setDesktopRuntimeForTests({ version: 1, instanceId: '6f17d720-9fab-48da-8dd5-9f3d270b2878' })
    const key = crypto.getRandomValues(new Uint8Array(32))
    let keyAvailable = true
    vi.stubGlobal('fetch', vi.fn(async () => keyAvailable
      ? new Response(JSON.stringify({ key: btoa(String.fromCharCode(...key)) }), { status: 200 })
      : new Response(null, { status: 503 })))
    const inbox = await openShareTargetInbox()
    const id = 'desktop-tamper'
    try {
      await inbox.clear()
      await inbox.save({ id, ownerAccountId: 11, createdAt: Date.now(), receivedOrder: 7,
        name: 'protected.jpg', type: 'image/jpeg', blob: new Blob(['protected-photo']),
        assignment: { kind: 'task', target: 'SDCFLEETOPS-7' } })
      const original = await rawShareDraft(id)

      for (const { mutation, accountId = 11 } of [
        { mutation: { assignment: { kind: 'task', target: 'SDCFLEETOPS-999' } } },
        { mutation: { name: 'forged.jpg' } },
        { mutation: { ownerAccountId: 12 }, accountId: 12 },
        { mutation: { createdAt: Number(original.createdAt) + 1 } },
        { mutation: { receivedOrder: 99 } },
        { mutation: { blobBytes: Number(original.blobBytes) + 1 } },
      ]) {
        await replaceRawShareDraft({ ...original, ...mutation })
        expect(await inbox.list(accountId)).toEqual([])
        await replaceRawShareDraft(original)
        expect(await inbox.list(11)).toMatchObject([{ id, ownerAccountId: 11,
          assignment: { kind: 'task', target: 'SDCFLEETOPS-7' } }])
      }

      await replaceRawShareDraft({ ...original, id: 'desktop-relocated' })
      expect((await inbox.list(11)).map(item => item.id)).toEqual([id])

      resetDesktopStorageKeyForTests()
      keyAvailable = false
      expect(await inbox.list(11)).toEqual([])
      expect(await rawShareDraft(id)).toEqual(original)
      resetDesktopStorageKeyForTests()
      keyAvailable = true
      const restored = (await inbox.list(11))[0]
      expect(restored).toMatchObject({ id, ownerAccountId: 11, assignment: { kind: 'task', target: 'SDCFLEETOPS-7' } })
      expect(await restored?.blob.text()).toBe('protected-photo')
    } finally {
      await inbox.clear(); inbox.close?.(); setDesktopRuntimeForTests(undefined); resetDesktopStorageKeyForTests(); vi.unstubAllGlobals()
    }
  })
  it('updates a desktop draft only through authenticated claim, assignment and discard transitions', async () => {
    setDesktopRuntimeForTests({ version: 1, instanceId: '6f17d720-9fab-48da-8dd5-9f3d270b2878' })
    const key = crypto.getRandomValues(new Uint8Array(32))
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ key: btoa(String.fromCharCode(...key)) }), { status: 200 })))
    const inbox = await openShareTargetInbox()
    try {
      await inbox.clear()
      await inbox.save({ id: 'desktop-transitions', ownerAccountId: null, createdAt: Date.now(), name: 'robot.jpg',
        type: 'image/jpeg', blob: new Blob(['photo']), assignment: null })
      expect(await inbox.claim('desktop-transitions', 11)).toMatchObject({ ownerAccountId: 11, assignment: null })
      await inbox.assign(11, 'desktop-transitions', { kind: 'task', target: 'SDCFLEETOPS-11' })
      expect(await inbox.list(11)).toMatchObject([{ assignment: { kind: 'task', target: 'SDCFLEETOPS-11' } }])
      await inbox.discard(12, 'desktop-transitions')
      expect(await inbox.list(11)).toHaveLength(1)
      await inbox.discard(11, 'desktop-transitions')
      expect(await inbox.list(11)).toEqual([])
    } finally {
      await inbox.clear(); inbox.close?.(); setDesktopRuntimeForTests(undefined); resetDesktopStorageKeyForTests(); vi.unstubAllGlobals()
    }
  })
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
