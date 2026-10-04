import { expect, test, type Page } from '@playwright/test'
import type { User } from '../src/api'
import { assertNoSeriousA11yViolations } from './support/assertA11y'
import { installMockApi, type MockRoute } from './support/mockApi'
import { mechanicUser, northPark, operatorUser } from './support/users'

const readyStatus = {
  supported: true, installed: true, enabled: true, ready: true, reason: null,
  model: 'prism-ml/Ternary-Bonsai-2-27B-gguf', backend: 'cuda', can_manage: false,
  counts: { documents: 2, candidates: 1, jobs: 0 },
}

const adminUser: User = {
  ...operatorUser,
  id: 40,
  username: 'admin-e2e',
  role: 'admin',
}

function emptyManagementRoutes(): MockRoute[] {
  return [
    { method: 'GET', path: '/api/ai/automations', handler: () => ({ json: [] }) },
    { method: 'GET', path: '/api/ai/scripts', handler: () => ({ json: [] }) },
    { method: 'GET', path: '/api/ai/connectors', handler: () => ({ json: [] }) },
    { method: 'GET', path: '/api/ai/config', handler: () => ({ json: { enabled: true, learning_enabled: true, revision: 1 } }) },
    { method: 'GET', path: '/api/ai/prompts', handler: () => ({ json: [] }) },
    { method: 'GET', path: '/api/ai/runs', handler: () => ({ json: [] }) },
  ]
}

async function openAssistant(page: Page, user: User, routes: MockRoute[]) {
  await installMockApi(page, { user, parks: [northPark], routes })
  await page.goto('/assistant?park_id=7')
  await expect(page.getByRole('heading', { name: 'Локальный помощник' })).toBeVisible()
}

test('desktop mechanic receives a sourced answer and opens the cited document', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  let answered = false
  await openAssistant(page, mechanicUser, [
    { method: 'GET', path: '/api/ai/status', handler: () => ({ json: readyStatus }) },
    { method: 'GET', path: '/api/ai/conversations', handler: () => ({ json: [{ id: 'c-1', title: 'Диагностика лидара', park_id: 7, issue_key: 'RP-42', updated_at: '2026-10-04T10:00:00Z' }] }) },
    { method: 'GET', path: '/api/ai/conversations/c-1', handler: () => ({ json: {
      id: 'c-1', title: 'Диагностика лидара', park_id: 7, issue_key: 'RP-42', updated_at: '2026-10-04T10:00:00Z', jobs: [],
      messages: answered ? [{ id: 'm-1', role: 'assistant', content: 'Отключите питание и проверьте разъём лидара.', created_at: '2026-10-04T10:01:00Z', sources: [{ id: 'd-1', title: 'Проверка лидара', excerpt: 'Перед осмотром отключите питание.', trust: 'instruction' }] }] : [],
    } }) },
    { method: 'POST', path: '/api/ai/conversations/c-1/messages', handler: () => { answered = true; return { json: { id: 'j-1', kind: 'chat', state: 'queued', created_at: '', updated_at: '', error: null, result: null } } } },
    { method: 'GET', path: '/api/ai/jobs/j-1', handler: () => ({ json: { id: 'j-1', kind: 'chat', state: 'succeeded', created_at: '', updated_at: '', error: null, result: {} } }) },
    { method: 'GET', path: '/api/ai/documents', handler: () => ({ json: { items: [], total: 0, offset: 0, limit: 30 } }) },
    { method: 'GET', path: '/api/ai/documents/d-1', handler: () => ({ json: { id: 'd-1', title: 'Проверка лидара', kind: 'manual', state: 'active', trust: 'instruction', park_id: 7, source_ref: 'manual:lidar', updated_at: '', revision: 1, content: 'Перед осмотром отключите питание.' } }) },
  ])

  await page.getByLabel('Сообщение помощнику').fill('Как проверить лидар?')
  await page.getByRole('button', { name: 'Отправить' }).click()
  const source = page.getByRole('link', { name: 'Проверка лидара' })
  await expect(source).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('mechanic-chat-desktop.png'), fullPage: true, animations: 'disabled' })
  await source.click()
  await expect(page.getByRole('heading', { name: 'Проверка лидара' })).toBeVisible()
  await expect(page.getByText('Перед осмотром отключите питание.')).toBeVisible()
  await assertNoSeriousA11yViolations(page)
})

test('admin imports knowledge at 320px with explicit activation choices', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 320, height: 800 })
  let importBody: Record<string, unknown> | null = null
  await openAssistant(page, adminUser, [
    { method: 'GET', path: '/api/ai/status', handler: () => ({ json: { ...readyStatus, can_manage: true } }) },
    { method: 'GET', path: '/api/ai/conversations', handler: () => ({ json: [] }) },
    { method: 'GET', path: '/api/ai/documents', handler: () => ({ json: { items: [], total: 0, offset: 0, limit: 30 } }) },
    { method: 'POST', path: '/api/ai/documents/import', handler: async request => { importBody = await request.json() as Record<string, unknown>; return { json: { created: 2, duplicates: 0, rejected: 0 } } } },
    ...emptyManagementRoutes(),
  ])

  await page.getByRole('tab', { name: 'База знаний' }).click()
  await page.getByText('Пакетный импорт JSON/JSONL').click()
  await page.getByLabel('Файл базы знаний').setInputFiles({
    name: 'seed.jsonl', mimeType: 'application/x-ndjson',
    buffer: Buffer.from([
      JSON.stringify({ title: 'Проверка лидара', content: 'Отключите питание.', kind: 'manual', source_ref: 'seed:1' }),
      JSON.stringify({ title: 'Опыт ремонта', content: 'Проверили разъём.', kind: 'ticket', source_ref: 'seed:2' }),
    ].join('\n')),
  })
  await page.getByLabel('Сразу активировать инструкции').check()
  await page.getByText('Использовать тикеты, переписку и заметки как непроверенный опыт').click()
  await page.getByRole('button', { name: 'Импортировать' }).click()

  await expect(page.getByRole('status').filter({ hasText: 'Добавлено: 2' })).toBeVisible()
  expect(importBody).toMatchObject({ park_id: 7, activate_manuals: true, activate_unverified: true })
  expect((importBody?.documents as unknown[])).toHaveLength(2)
  await page.locator('.rp-assistant-knowledge').screenshot({
    path: testInfo.outputPath('admin-import-mobile-320.png'), animations: 'disabled',
    style: '.rp-shell__skip-link, .rp-shell__topbar, .rp-shell__bottom-nav { visibility: hidden !important; }',
  })
  await expect(page.locator('html')).toHaveJSProperty('scrollWidth', 320)
  await assertNoSeriousA11yViolations(page)
})

test('unsupported host only requests status and stays read-only', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1024, height: 768 })
  const aiRequests: string[] = []
  page.on('request', request => {
    const pathname = new URL(request.url()).pathname
    if (pathname.startsWith('/api/ai/')) aiRequests.push(`${request.method()} ${pathname}`)
  })
  await installMockApi(page, {
    user: mechanicUser,
    parks: [northPark],
    routes: [{ method: 'GET', path: '/api/ai/status', handler: () => ({ json: { ...readyStatus, supported: false, ready: false, installed: false, enabled: false, backend: null, reason: 'p3701_required' } }) }],
  })

  await page.goto('/assistant?park_id=7')

  await expect(page.getByRole('heading', { name: 'Требуется NVIDIA AGX Orin' })).toBeVisible()
  await expect(page.getByText('Требуется NVIDIA AGX Orin P3701.')).toBeVisible()
  expect(aiRequests.length).toBeGreaterThan(0)
  expect(new Set(aiRequests)).toEqual(new Set(['GET /api/ai/status']))
  await expect(page.getByRole('button', { name: 'Отправить' })).toHaveCount(0)
  await assertNoSeriousA11yViolations(page)
  await page.screenshot({ path: testInfo.outputPath('unsupported-host.png'), fullPage: true, animations: 'disabled' })
})

test('admin cleans old history and sees accurate conversation and job counts', async ({ page }) => {
  let cleanupBody: unknown
  await openAssistant(page, adminUser, [
    { method: 'GET', path: '/api/ai/status', handler: () => ({ json: { ...readyStatus, can_manage: true } }) },
    { method: 'GET', path: '/api/ai/conversations', handler: () => ({ json: [] }) },
    { method: 'POST', path: '/api/ai/maintenance', handler: async request => {
      cleanupBody = await request.json()
      return { json: { deleted: 1, conversations_deleted: 1, jobs_deleted: 5, messages_deleted: 2 } }
    } },
    ...emptyManagementRoutes(),
  ])
  await page.getByRole('tab', { name: 'Настройки и журнал' }).click()
  await page.getByText('Очистка журналов', { exact: true }).click()
  await page.getByRole('button', { name: 'Очистить историю старше 30 дней' }).click()

  await expect(page.getByText('Удалено бесед: 1; заданий: 5; сообщений: 2')).toBeVisible()
  expect(cleanupBody).toEqual({ kind: 'history', before_days: 30 })
})


test('conversation creation retries once and ignores a late response after another selection', async ({ page }) => {
  const first = { id: 'c-1', title: 'Первый разговор', park_id: 7, issue_key: null, updated_at: '', messages: [], jobs: [] }
  const second = { ...first, id: 'c-2', title: 'Второй разговор' }
  let creations = 0
  let finishCreate!: () => void
  const pendingCreate = new Promise<void>(resolve => { finishCreate = resolve })
  await openAssistant(page, mechanicUser, [
    { method: 'GET', path: '/api/ai/status', handler: () => ({ json: readyStatus }) },
    { method: 'GET', path: '/api/ai/conversations', handler: () => ({ json: [first, second] }) },
    { method: 'GET', path: '/api/ai/conversations/c-1', handler: () => ({ json: first }) },
    { method: 'GET', path: '/api/ai/conversations/c-2', handler: () => ({ json: second }) },
    { method: 'POST', path: '/api/ai/conversations', handler: async () => {
      creations += 1
      if (creations === 1) return { status: 503, json: { detail: 'temporarily_unavailable' } }
      await pendingCreate
      return { json: { ...first, id: 'c-late', title: 'Поздний разговор' } }
    } },
  ])
  const create = page.getByRole('button', { name: 'Новый', exact: true })
  const draft = page.getByLabel('Сообщение помощнику')
  await draft.fill('Проверить питание')
  await create.click()
  await expect(page.getByRole('alert')).toContainText('Не удалось создать разговор')
  await expect(create).toBeEnabled()
  await expect(draft).toHaveValue('Проверить питание')
  await create.click()
  await expect(create).toBeDisabled()
  await expect(draft).toBeDisabled()
  expect(creations).toBe(2)
  await page.getByRole('button', { name: 'Второй разговор', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Второй разговор' })).toBeVisible()
  const response = page.waitForResponse(value => value.request().method() === 'POST' && new URL(value.url()).pathname === '/api/ai/conversations' && value.status() === 200)
  finishCreate()
  await response
  await expect(page.getByRole('heading', { name: 'Второй разговор' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Поздний разговор', exact: true })).toHaveCount(0)
  await expect(draft).toBeEnabled()
  await expect(draft).toHaveValue('Проверить питание')
  expect(creations).toBe(2)
})

test('failed conversation deletion can be retried without clearing a newer selection', async ({ page }) => {
  const first = { id: 'c-1', title: 'Первый разговор', park_id: 7, issue_key: null, updated_at: '', messages: [], jobs: [] }
  const second = { ...first, id: 'c-2', title: 'Второй разговор' }
  let deletions = 0
  let finishDelete!: () => void
  const pendingDelete = new Promise<void>(resolve => { finishDelete = resolve })
  await openAssistant(page, mechanicUser, [
    { method: 'GET', path: '/api/ai/status', handler: () => ({ json: readyStatus }) },
    { method: 'GET', path: '/api/ai/conversations', handler: () => ({ json: [first, second] }) },
    { method: 'GET', path: '/api/ai/conversations/c-1', handler: () => ({ json: first }) },
    { method: 'GET', path: '/api/ai/conversations/c-2', handler: () => ({ json: second }) },
    { method: 'DELETE', path: '/api/ai/conversations/c-1', handler: async () => {
      deletions += 1
      if (deletions === 1) return { status: 503, json: { detail: 'temporarily_unavailable' } }
      await pendingDelete
      return { json: { deleted: true } }
    } },
  ])
  const remove = page.getByRole('button', { name: 'Удалить Первый разговор', exact: true })
  const draft = page.getByLabel('Сообщение помощнику')
  await draft.fill('Проверить питание')
  await remove.click()
  await expect(page.getByRole('alert')).toContainText('Не удалось удалить разговор')
  await expect(remove).toBeEnabled()
  await expect(page.getByRole('heading', { name: 'Первый разговор' })).toBeVisible()
  await remove.click()
  await expect(remove).toBeDisabled()
  expect(deletions).toBe(2)
  await page.getByRole('button', { name: 'Второй разговор', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Второй разговор' })).toBeVisible()
  finishDelete()
  await expect(remove).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Первый разговор', exact: true })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Второй разговор' })).toBeVisible()
  await expect(draft).toHaveValue('Проверить питание')
  await expect(draft).toBeEnabled()
  expect(deletions).toBe(2)
})
