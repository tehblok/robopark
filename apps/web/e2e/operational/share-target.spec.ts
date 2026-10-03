import { expect, test } from '@playwright/test'
import { installOperational, userForRole } from './fixtures'

test('a shared photo stays in its launched tab and claimed account', async ({ page, context }) => {
  const shareId = '01234567-89ab-4cde-8fab-0123456789ab'
  const secondId = '11234567-89ab-4cde-8fab-0123456789ab'
  await page.setViewportSize({ width: 390, height: 900 })
  await installOperational(page, { user: userForRole('mechanic') })
  await page.goto('/overview?park=7')
  await page.evaluate(async ids => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open('robopark-share-inbox-v2', 1)
      request.onupgradeneeded = () => request.result.createObjectStore('drafts', { keyPath: 'id' })
      request.onsuccess = () => resolve(request.result)
      request.onerror = () => reject(request.error)
    })
    await new Promise<void>((resolve, reject) => {
      const transaction = db.transaction('drafts', 'readwrite')
      ids.forEach((id, index) => transaction.objectStore('drafts').put({ id, createdAt: Date.now() + index,
        name: index === 0 ? 'private-robot.jpg' : 'second-robot.jpg', type: 'image/jpeg',
        blob: new Blob(['photo'], { type: 'image/jpeg' }), assignment: null, ownerAccountId: null }))
      transaction.oncomplete = () => resolve()
      transaction.onerror = () => reject(transaction.error)
    })
    db.close()
  }, [shareId, secondId])

  await page.goto(`/?shared=${shareId}`)
  await expect(page.getByRole('complementary', { name: 'Полученное фото' })).toContainText('private-robot.jpg')
  await expect(page.getByRole('heading', { name: 'Работа', exact: true })).toBeVisible()
  await expect(page).not.toHaveURL(/shared=/)
  await page.screenshot({ path: '/tmp/robopark-share-target-scoped-phone.png' })

  await page.goto(`/?shared=${secondId}`)
  await expect(page.getByRole('complementary', { name: 'Полученное фото' })).toContainText('private-robot.jpg')
  await page.getByRole('button', { name: 'Удалить черновик private-robot.jpg' }).click()
  await expect(page.getByRole('complementary', { name: 'Полученное фото' })).toContainText('second-robot.jpg')
  await page.getByRole('button', { name: 'Удалить черновик second-robot.jpg' }).click()
  await page.goto('/?shared=1')
  await expect(page.getByRole('complementary', { name: 'Фото из старой версии' })).toContainText('Отправьте фото ещё раз')
  await page.getByRole('button', { name: 'Понятно' }).click()
  await expect(page.getByRole('complementary', { name: 'Фото из старой версии' })).toHaveCount(0)

  const other = await context.newPage()
  await other.setViewportSize({ width: 390, height: 900 })
  await installOperational(other, { user: userForRole('operator') })
  await other.goto('/overview?park=7')
  await expect(other.getByRole('complementary', { name: 'Полученное фото' })).toHaveCount(0)
  await other.evaluate(id => sessionStorage.setItem('robopark:share-target-ids', JSON.stringify([id])), secondId)
  await other.reload()
  await expect(other.getByRole('heading', { name: 'Что требует решения сейчас' })).toBeVisible()
  await expect(other.getByRole('complementary', { name: 'Полученное фото' })).toHaveCount(0)
  await expect.poll(() => other.evaluate(() => sessionStorage.getItem('robopark:share-target-ids'))).toBe('[]')
})
