// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { IDBFactory, IDBObjectStore } from 'fake-indexeddb'
import { clearReportPhotoDrafts, deleteReportPhotoDraft, readReportPhotoDraft, writeReportPhotoDraft, type ReportPhotoDraft } from './reportPhotoDrafts'

function draft(key = 'robopark:report-draft:1:7', revision = 'one'): ReportPhotoDraft {
  return { key, revision, ownerKey: 'user-1-park-7', activeForm: 'problem', title: 'Колесо', body: 'Не едет', trackerKey: '', createdReportId: 42,
    attachmentKind: 'device_photo', attachment: { blob: new Blob(['photo'], { type: 'image/jpeg' }), name: 'robot.jpg', lastModified: 123 } }
}
beforeEach(async () => { vi.stubGlobal('indexedDB', new IDBFactory()); await clearReportPhotoDrafts() })
describe('persistent report photos', () => {
  it('restores bytes, metadata and the already-created report only within its user and park', async () => {
    await writeReportPhotoDraft(draft())
    const restored = await readReportPhotoDraft('robopark:report-draft:1:7')
    expect(restored).toMatchObject({ title: 'Колесо', createdReportId: 42, attachment: { name: 'robot.jpg', lastModified: 123 } })
    expect(await restored?.attachment?.blob.text()).toBe('photo')
    expect(await readReportPhotoDraft('robopark:report-draft:2:7')).toBeNull()
    expect(await readReportPhotoDraft('robopark:report-draft:1:8')).toBeNull()
  })
  it('clears only the revision that was successfully submitted', async () => {
    await writeReportPhotoDraft(draft())
    await writeReportPhotoDraft({ ...draft(undefined, 'two'), title: 'Новая правка' })
    await deleteReportPhotoDraft('robopark:report-draft:1:7', 'one')
    expect((await readReportPhotoDraft('robopark:report-draft:1:7'))?.title).toBe('Новая правка')
    await deleteReportPhotoDraft('robopark:report-draft:1:7', 'two')
    expect(await readReportPhotoDraft('robopark:report-draft:1:7')).toBeNull()
  })
  it('revokes queued saves when logout or owner deletion occurs', async () => {
    const save = writeReportPhotoDraft(draft())
    const clear = clearReportPhotoDrafts()
    await Promise.all([save, clear])
    expect(await readReportPhotoDraft('robopark:report-draft:1:7')).toBeNull()
    const nextSave = writeReportPhotoDraft(draft())
    const remove = deleteReportPhotoDraft('robopark:report-draft:1:7')
    await Promise.all([nextSave, remove])
    expect(await readReportPhotoDraft('robopark:report-draft:1:7')).toBeNull()
  })
  it('rejects oversized files without replacing the previous stored draft', async () => {
    await writeReportPhotoDraft(draft())
    const huge = { ...draft(), attachment: { name: 'huge.jpg', lastModified: 0, blob: new Blob([new Uint8Array(15 * 1024 * 1024 + 1)]) } }
    await expect(writeReportPhotoDraft(huge)).rejects.toThrow(/15 МиБ/)
    expect(await (await readReportPhotoDraft('robopark:report-draft:1:7'))?.attachment?.blob.text()).toBe('photo')
  })
  it('bounds total records without silently deleting incomplete reports', async () => {
    for (let i = 0; i < 8; i++) await writeReportPhotoDraft(draft(`robopark:report-draft:${i}:7`))
    await expect(writeReportPhotoDraft(draft('robopark:report-draft:9:7'))).rejects.toThrow(/Удалите/)
    expect(await readReportPhotoDraft('robopark:report-draft:0:7')).not.toBeNull()
  })
  it('bounds text and total photo bytes as well as each individual photo', async () => {
    await expect(writeReportPhotoDraft({ ...draft(), body: 'x'.repeat(256 * 1024) })).rejects.toThrow(/Текст/)
    for (let i = 0; i < 4; i++) await writeReportPhotoDraft({ ...draft(`large:${i}`), attachment: { name: 'photo.jpg', lastModified: 0, blob: new Blob([new Uint8Array(15 * 1024 * 1024)]) } })
    await expect(writeReportPhotoDraft(draft('large:4'))).rejects.toThrow(/Удалите/)
    expect(await readReportPhotoDraft('large:0')).not.toBeNull()
  })

  it('rejects a revoked writer from another tab but allows a fresh authorized session', async () => {
    await writeReportPhotoDraft(draft())
    vi.resetModules()
    const oldTab = await import('./reportPhotoDrafts')
    await oldTab.readReportPhotoDraft('robopark:report-draft:1:7')
    await clearReportPhotoDrafts()
    await expect(oldTab.writeReportPhotoDraft(draft())).rejects.toThrow(/Сеанс/)
    await expect(oldTab.writeReportPhotoDraft(draft('unseen-park'))).rejects.toThrow(/Сеанс/)
    expect(await readReportPhotoDraft('robopark:report-draft:1:7')).toBeNull()
    vi.resetModules()
    const newSession = await import('./reportPhotoDrafts')
    await newSession.readReportPhotoDraft('robopark:report-draft:1:7')
    await newSession.writeReportPhotoDraft({ ...draft(), title: 'Новый сеанс' })
    expect((await newSession.readReportPhotoDraft('robopark:report-draft:1:7'))?.title).toBe('Новый сеанс')
  })
  it('revokes another tab on a scoped owner change without removing other parks', async () => {
    await writeReportPhotoDraft(draft())
    await writeReportPhotoDraft(draft('robopark:report-draft:1:8'))
    vi.resetModules()
    const oldTab = await import('./reportPhotoDrafts')
    await oldTab.readReportPhotoDraft('robopark:report-draft:1:7')
    await deleteReportPhotoDraft('robopark:report-draft:1:7')
    await expect(oldTab.writeReportPhotoDraft(draft())).rejects.toThrow(/Сеанс/)
    expect(await readReportPhotoDraft('robopark:report-draft:1:7')).toBeNull()
    expect(await readReportPhotoDraft('robopark:report-draft:1:8')).not.toBeNull()
  })

  it.each(['logout', 'owner change'])('does not rebind an old session when %s happens during its generation read', async (revocation) => {
    await writeReportPhotoDraft(draft())
    let clearing!: Promise<void>
    const originalGet = IDBObjectStore.prototype.get
    const get = vi.spyOn(IDBObjectStore.prototype, 'get').mockImplementationOnce(function (this: IDBObjectStore, key) {
      const request = originalGet.call(this, key)
      // The transaction has started, but its generation reply has not arrived.
      clearing = revocation === 'logout' ? clearReportPhotoDrafts() : deleteReportPhotoDraft('robopark:report-draft:1:7')
      return request
    })
    await writeReportPhotoDraft({ ...draft(), title: 'Старый запрос' })
    await clearing
    get.mockRestore()
    expect(await readReportPhotoDraft('robopark:report-draft:1:7')).toBeNull()
    await writeReportPhotoDraft({ ...draft(), title: 'Новый сеанс после выхода' })
    expect((await readReportPhotoDraft('robopark:report-draft:1:7'))?.title).toBe('Новый сеанс после выхода')
  })

})
