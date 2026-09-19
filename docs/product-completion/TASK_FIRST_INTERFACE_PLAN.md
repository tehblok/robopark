# Полная переработка интерфейса А — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (рекомендация для экономии токенов) или superpowers:subagent-driven-development при выборе пользователем. Выполнять этапы по порядку, с независимым финальным ревью.

**Goal:** Все экраны и роли работают в новом UX А и в сохранённом классическом интерфейсе с общими данными, безопасным переключением и PWA.

**Architecture:** Существующие route gates, модели, запросы и команды остаются общими. У каждого раздела один владелец состояния, а режим меняет композицию и презентацию; второй интерфейс не монтируется скрыто. Новый CSS и тяжёлые модули загружаются по необходимости.

**Tech Stack:** React, TypeScript, Vite, существующий resourceStore, Vitest, Playwright, существующий service worker. Без нового UI-фреймворка или второго backend.

**Spec:** `docs/product-completion/TASK_FIRST_INTERFACE_DESIGN.md` — согласована пользователем.

## Global Constraints

- «Классический» / «Новый А». По умолчанию — классический, переход добровольный.
- Эталон: frontend 5b465cc плюс `docs/prototypes/robot-check-ux/proposal.css` для проверки робота.
- Роли: mechanic, operator, driver, admin, royal; индивидуальные разрешения сохраняются.
- API, фото тикетов, cookie и авторизационные ответы не добавляются в постоянный офлайн-кэш.
- Проверка прав, актуальных остатков, идемпотентность и действия Tracker остаются на сервере.
- Основные области нажатия не менее 44 px.
- Все разделы и вложенные формы входят в результат; fallback на старую страницу не считается переработкой.
- По последнему запросу OTA входит в конечный результат; установка на хост автоматически не выполняется. Рабочие данные не менять тестами.

## Review Focus

1. Смена режима при выбранном фото или набранном комментарии: не теряется File/текст и не повторяется запрос — этапы 1–3.
2. Другой аккаунт/парк или 403 во время фонового запроса: прежние защищённые данные не возвращаются — этапы 1, 7, 8.
3. Потеря Wi-Fi во время списания/закрытия: нет ложного успеха или автоматического повторного списания — этапы 3–4, 8.
4. Обновление PWA со старой вкладкой: нет смешения несовместимых assets и внезапного сброса формы — этап 8.
5. Вложенные настройки, длинные названия, клавиатура телефона и индивидуальные права: не скрываются действия и доступы — этапы 2, 5–7.

## Общие контракты этапов

Новые файлы `apps/web/src/app/interface/interfaceModeStore.ts`,
`InterfaceModeProvider.tsx`, `InterfaceChoice.tsx`, `interface-a.css`.

```ts
export type InterfaceMode = 'classic' | 'task-first'
export type InterfaceModeSnapshot = {
  mode: InterfaceMode
  pendingMode: InterfaceMode | null
  mutationCount: number
}
export interface InterfaceModeStore {
  getSnapshot(): InterfaceModeSnapshot
  subscribe(listener: () => void): () => void
  setAccount(accountId: number | null): void
  requestMode(mode: InterfaceMode): void
  beginMutation(): () => void
}
export type InterfaceStorage = Pick<Storage, 'getItem' | 'setItem'>
// Factory implemented in interfaceModeStore.ts; callers may inject throwing storage for tests.
export declare function createInterfaceModeStore(storage: () => InterfaceStorage): InterfaceModeStore
export type InterfaceModeContextValue = InterfaceModeSnapshot & {
  requestMode(mode: InterfaceMode): void
}
```

Store не содержит задачи/токены/права. Ключ настройки
`robopark:interface:v1:<accountId>`; анонимный выбор — только в памяти.
`beginMutation()` возвращает идемпотентный release; requestMode при активной
мутации записывает pendingMode. Последний release применяет выбор только в той
же сессии аккаунта. Logout сбрасывает активные ожидания.

Provider живёт выше маршрутов под AuthProvider, но смена режима не меняет key
роутера, ParkProvider и владельцев запросов. Атрибут `data-interface` ставится на
корневой элемент документа, чтобы портал диалога наследовал режим. Классические
стили не переписывать глобальными правилами нового режима.

Для E2E новый helper `apps/web/e2e/support/interfaceMode.ts`:

```ts
import { expect, type Page } from '@playwright/test'
export async function selectInterface(page: Page, label: 'Классический' | 'Новый А') {
  await page.getByRole('button', { name: 'Ещё', exact: true }).click()
  await page.getByRole('radio', { name: label, exact: true }).check()
  await expect(page.locator('html')).toHaveAttribute(
    'data-interface', label === 'Классический' ? 'classic' : 'task-first',
  )
  await page.keyboard.press('Escape')
}
```

Helper применяется после авторизации fixture. Public routes проверяются отдельным
переключателем в AuthLayout, без открытия аккаунта из storage.

### Task 1: Эталон, переключатель и сохранение состояния

**Файлы:** новые app/interface файлы выше и их `.test.tsx`/`.test.ts`;
`apps/web/src/main.tsx`, `app/shell/AppShell.tsx`, `api.ts`;
`domains/robots/robot-check.css`; `e2e/operational/interface-mode.spec.ts`.

- [ ] Зафиксировать скриншоты классического эталона. Подключить согласованную композицию проверки из prototype как classic-scoped правила, не весь прототипный runner. Документировать исходный SHA и размеры.
- [ ] Написать unit tests для store: default classic; два аккаунта; недоступный storage; отложенный выбор; два параллельных запроса; двойной release; смена аккаунта до release. Пример основной проверки:

```ts
const store = createInterfaceModeStore(() => localStorage)
store.setAccount(101)
const release = store.beginMutation()
store.requestMode('task-first')
expect(store.getSnapshot().mode).toBe('classic')
release()
expect(store.getSnapshot().mode).toBe('task-first')
release()
expect(store.getSnapshot().mutationCount).toBe(0)
```

- [ ] Запустить `npx vitest run src/app/interface`; получить ожидаемый RED до реализации store/provider.
- [ ] Реализовать contracts выше; обработку localStorage завернуть в try/catch, snapshot менять только при изменениях. Во внешнем request lifecycle общего API оборачивать небезопасные методы в beginMutation/try/finally, включая upload, не каждый сетевой retry отдельно:

```ts
export async function trackInterfaceMutation<T>(
  store: InterfaceModeStore, method: string, operation: () => Promise<T>,
): Promise<T> {
  const readOnly = ['GET', 'HEAD', 'OPTIONS'].includes(method.toUpperCase())
  const release = readOnly ? () => {} : store.beginMutation()
  try { return await operation() } finally { release() }
}
```

Добавить helper в `app/interface/interfaceModeStore.ts`; в общем транспорте передать
существующую функцию исполнения запроса как operation. Сетевую семантику не менять.

- [ ] Добавить radio group «Интерфейс» в меню, `pendingMode` → «Переключим после завершения операции». Сохранять живой компонент экрана при смене режима; не создавать два дерева с display:none.
- [ ] Запустить unit suite, `interface-mode.spec.ts`, build; проверить классический baseline. Коммит `feat(web): add account-scoped interface mode`.

### Task 2: Новая оболочка, public screens и общий UX

**Файлы:** `app/shell/AppShell.tsx`/CSS; `app/routing/AppRouter.tsx`;
`components/auth/AuthLayout.tsx`; `pages/{Home,Login,Register,ChangePassword,NoCabinet,MechanicNoPark,OperatorPending,OperatorRejected}.tsx`;
`design-system/layout/{PageLayout,MasterDetail}.tsx`, `app/interface/interface-a.css`;
`e2e/operational/{workspace-navigation,interface-mode,route-role-layout}.spec.ts`.

- [ ] RED: для каждого public route и пяти ролей проверить явный переход к новому режиму и назад, доступные разделы, отсутствие недоступных ссылок, сохранение парка/query. Не фиксировать порядок маршрутов через копию массива — брать routeManifest и проверять доступ через фактический UI.
- [ ] Новый shell: на ПК компактная левая навигация и контекст страницы, на телефоне ролевые нижние разделы + «Ещё». Выбор парков и настройки доступны с любого рабочего экрана. Выделять активный раздел не только цветом.
- [ ] Для нового режима добавить явные слоты основной области, контекста и действий в PageLayout, без второго владельца данных:

```tsx
type TaskFirstSlots = { primary: React.ReactNode; context?: React.ReactNode; actions?: React.ReactNode }
// Presentation only: без useEffect загрузки и без api.
function TaskFirstLayout({ primary, context, actions }: TaskFirstSlots) {
  return <div className="a-layout"><section className="a-primary">{primary}</section>
    {context && <aside className="a-context">{context}</aside>}
    {actions && <div className="a-actions">{actions}</div>}</div>
}
```

Файл `apps/web/src/app/interface/TaskFirstLayout.tsx`. Элементы форм с локальным
состоянием нельзя переносить между разными родителями при выборе режима без
поднятия draft выше переключаемой презентации. Для общих элементов предпочтительны
стабильный DOM и CSS grid-area. CSS А ограничить `html[data-interface='task-first']`.

- [ ] Переработать public screens: одно главное действие, обратная связь поля рядом с ним, отдельные состояния ожидания/отказа/нет парка; role gate остаётся общим. Перед входом выбор режима не читает preference другого аккаунта.
- [ ] Проверить 320/390/1440, темы, фокус, landscape/клавиатуру, reduced motion. Запустить полный web suite/build. Коммит `feat(web): introduce task-first shell and access screens`.

### Task 3: Работа и роботы — полноценный сценарий А

**Файлы:** `domains/work/{WorkPage,IssueWorkbench}.tsx`; `domains/robots/{RobotsPage,RobotPage,RobotCheckPage,RobotCheckWorkspace,RobotCheckSummary,RobotDiagnosticDiagram}.tsx`;
новые presentation-файлы `domains/work/TaskFirstWorkbench.tsx`, `domains/robots/TaskFirstRobotLayout.tsx`;
`e2e/operational/{work,work-tabs,task-lifecycle,task-collaboration,robot-qr,robots,responsive-visual}.spec.ts`.

- [ ] RED: открыть тестовый тикет, выбрать А, заполнить комментарий и файл, переключить classic→А; проверить текст/File, URL, владельца задачи. Отдельно удержать deferred ответ закрытия и проверить отсутствие второй отправки.
- [ ] Сохранить владельцем fetch/mutations существующий WorkPage/IssueWorkbench. Поднять draft комментария/фото/кода дефекта выше сменяемых presentation-компонентов, если текущий DOM нельзя сохранить. Обе презентации используют одни callbacks, а не разные реализации команды.
- [ ] А: в списке сначала «Мои задачи», затем рабочая очередь со статусом, SLA и фильтром. В тикете три области «Ремонт / Проверка / Чат»; ремонт содержит описание, запчасти, передачу смены, результат. Контекст на ПК — робот и текущая ошибка; на телефоне раскрываемая сводка. Не дублировать две проверки робота в основной области и контексте.
- [ ] Реальные формы завершения, операторского возврата, скрытия, чата и вложений встроить целиком. Для переключения вкладок сохранять draft в owner, но ненужные camera streams/polling останавливать.
- [ ] Роботы: поиск/сканирование → карточка → проверка/задачи. В проверке сохраняются отдельные диагностические блоки с реальной разметкой, ошибки/игнор, LTE/SIM, АКБ, карта и технические данные. Человеческое объяснение впереди сырого кода; сырой код доступен в подробностях. Отсутствие показаний не заменяется нормой.
- [ ] Сетевой regression: считать реальные intercepted GET для одного snapshot; переключение режима со свежими данными не увеличивает счётчик. Для denied response проверить исчезновение защищённого содержимого в обоих режимах.
- [ ] Прогнать перечисленные E2E в обоих режимах + полный suite/build. Проверить комментарий, фото, дефект и статус в контрактных тестах без production Tracker. Коммит `feat(web): rebuild work and robot workflows in interface A`.

### Task 4: Склад, поставки и инвентаризация

**Файлы:** `domains/inventory/{InventoryPage,InventoryPartsView,InventoryManageView,InventoryReceiptsView,InventoryCountsView,InventoryExportView,InventoryTabs}.tsx`;
`e2e/operational/inventory-workflows.spec.ts`, новый `interface-inventory.spec.ts`.

- [ ] RED для mechanic/operator/admin/royal: поиск → место/остаток; приход; инвентаризация; export/печать по существующим правам. Operator видит остатки, но не кнопки записи/печати. При смене режима сохраняются строки документа/количества/фото.
- [ ] Компоновка А: поиск и фильтр компонента наверху; читаемые строки запчастей с небольшим фото, местом и остатком. Поставки/инвентаризация — список документов и выбранный документ на ПК, последовательный переход на телефоне; основные действия документа в одном месте, вторичные раскрываются.
- [ ] Сохранить существующие document controllers. Перевести optional KPI fetch InventoryPage на общий resourceStore с ключом account/access/park; инвалидировать по INVENTORY_REVISION_CHANGED, не по режиму. Сохранить generation-защиту смены парка.
- [ ] В тестах задержать post документа, повторно нажать и сменить режим; assert одна мутация и отображение подтверждённого результата. При offline/409 показывать конфликт, не уменьшать остаток как подтверждённый.
- [ ] Снять оба режима с длинным артикулом, отсутствующим фото/остатком, сотнями результатов с пагинацией; проверить этикетки/экспорт отдельно. Полный suite/build. Коммит `feat(web): rebuild inventory workflows in interface A`.

### Task 5: Репорты и сервисные кампании

**Файлы:** `pages/Reports.tsx`, `components/reports/{ReportList,ReportDetail,ReportForms}.tsx`;
`domains/campaigns/CampaignsPage.tsx`; `e2e/operational/{admin-reports,report-photo-drafts}.spec.ts`;
новый `interface-campaigns.spec.ts`.

- [ ] RED: admin/royal видят все разрешённые уровни репортов, остальные свои разрешённые; подтверждение необратимого удаления; draft с фото жив после выбора дизайна. Кампания создаётся и открывается в обоих режимах через fixture API.
- [ ] Репорты А: входящие/свои и фильтры рядом со списком; карточка — содержание/чат/вложения и один блок обработки, удаление в действиях с явным подтверждением. Не менять серверные выборки видимости.
- [ ] Кампании А: прогресс/сроки/парки в заголовке, открытые и закрытые тикеты с поиском по роботу; редактирование правил отбора отдельно от выполнения. На телефоне не пытаться уместить две колонки тикетов рядом.
- [ ] Переиспользовать existing controllers и callbacks; формы creation/edit сохраняют draft вне сменяемой презентации. File не сериализовать в localStorage. UI удаляет карточку только после успешного API; ошибка сохраняет её.
- [ ] Прогнать E2E с 403, пустым списком, ошибкой отправки фото, повтором; полный suite/build. Коммит `feat(web): rebuild reports and campaigns in interface A`.

### Task 6: Обзор, аналитика и парки оператора

**Файлы:** `domains/shift/{OverviewPage,OverviewSections}.tsx`; `domains/analytics/AnalyticsWorkspace.tsx`;
`domains/insights/{InsightsPage,OperationsPanels}.tsx`; `pages/OperatorParks.tsx`;
`e2e/operational/{overview,analytics,insight-parks}.spec.ts`.

- [ ] RED: пять ролей, разрешённые быстрые переходы, текущий парк, возврат со страницы объекта к тем же фильтрам; deferred 403 не возвращает кэш после смены режима.
- [ ] Обзор А строить вокруг ожидающих действий: механик — его ремонт/остатки/СК; оператор — приёмка/обращения; водитель — разрешённые роботы/репорты; admin/royal — состояние парков и исключения. Не добавлять новые метрики, не имеющие источника.
- [ ] Аналитика: фильтры в одном месте, основной график и раскрываемая детализация, читабельные подписи на телефоне. Сохранить модель/агрегацию и кэш. Парки оператора: текущие подключения, доступные парки и статусы заявок как отдельные понятные секции.
- [ ] Проверить период/парки в URL, пустые/ошибочные данные, stale labels; полный suite/build. Коммит `feat(web): rebuild overview and analytics in interface A`.

### Task 7: Управление и настройка диагностики

**Файлы:** `domains/management/{ManagementPage,UserManagementPage,RoleManagementPage}.tsx`;
`pages/{Admin,AdminEmergencyConfig}.tsx`; `components/admin/{AdminUsersPanel,AdminRolesPanel,AdminOpsPanel,SystemHealthPanel,HostHealthPanel}.tsx`;
`domains/diagnostics/{DiagnosticRuleEditor,UnknownDiagnosticInbox,ReadingCatalogEditor}.tsx`;
`e2e/operational/{admin-settings,ops,host-health,diagnostic-editor,diagnostic-rules,diagnostic-unknowns,diagnostic-markers}.spec.ts`.

- [ ] RED: вложенные вкладки и формы, effective permissions для индивидуальной роли; смена режима при редактировании прав/разметки не теряет значения, save не отправляется дважды.
- [ ] Управление А: навигация по сущностям, список + выбранный пользователь/роль/парк на ПК, отдельный detail на телефоне; действия не смешивать с диагностикой хоста. Для опасных операций сохранить явные подтверждения.
- [ ] Настройка робота А: выбор ошибки/показания → изображение с текущей разметкой → редактирование → предпросмотр. Игнорирование и неизвестные ошибки доступны явно. Не менять координатную модель/единицы/правила сопоставления.
- [ ] OTA/диагностика А: текущая операция, прогресс/ошибка/восстановление, доступные действия; кнопки учитывают реально активную операцию. Режим не запускает дополнительные polling/job requests.
- [ ] Проверить по E2E все вложенные вкладки, focus/touch targets, недоступные права, незавершённые операции; полный suite/build. Коммит `feat(web): rebuild management and diagnostics editors in interface A`.

### Task 8: Общая приёмка, PWA и нагрузка

**Файлы:** `e2e/operational/route-role-layout.spec.ts`, новый `interface-parity.spec.ts`;
`scripts/{build-sw.mjs,sw-template.js}`, существующие SW tests;
новый `apps/web/scripts/interface-load.mjs`, отчёт `docs/product-completion/INTERFACE_A_ACCEPTANCE.md`.

- [ ] Расширить route matrix двумя режимами. Обязательная проверка покрытия:

```ts
import { test, expect } from '@playwright/test'
import { SYSTEM_USER_ROLES, ROUTE_MANIFEST } from '../../src/app/routing/routeManifest'
import { canAccessRoute } from '../../src/app/routing/accessPolicy'
import { openRouteFixture } from './routeFixtures'
import { userForRole } from './fixtures'
import { selectInterface } from '../support/interfaceMode'

for (const mode of ['classic', 'task-first'] as const)
  for (const role of SYSTEM_USER_ROLES)
    for (const route of ROUTE_MANIFEST.filter(item => item.surface === 'shell' && !item.redirectTo)) {
      const user = userForRole(role)
      if (!canAccessRoute(user, route.id)) continue // Separate denied suite below, not a pass.
      test(`${role} ${route.id} ${mode}`, async ({ page }) => {
        await openRouteFixture(page, route.id, user)
        await selectInterface(page, mode === 'classic' ? 'Классический' : 'Новый А')
        await expect(page.locator('main')).toBeVisible()
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      })
    }
```

В test body использовать openRouteFixture для разрешённых маршрутов, а denied
cases открывать напрямую после installOperational и проверять отсутствие защищённого
контента. Для public routes проверять оба режима без аккаунта. Отдельный перечень
вложенных сценариев из этапов 3–7 приложить к отчёту, не заменять его route count.

- [ ] Проверить storage/cache isolation, 50 циклов mode/navigation/photo, число активных запросов/таймеров, отсутствие живых camera tracks после закрытия. Не требовать нулевых колебаний heap: сравнивать удерживаемые ресурсы после idle/GC в отдельном инструментированном запуске.
- [ ] PWA tests: offline HTML, API не кэшируется, приватный attachment не попадает в CacheStorage, смена версии assets, отказ загрузки chunk даёт возврат к classic без потери draft. Полная перезагрузка только после явного согласия при черновике.
- [ ] Load script принимает только localhost/127.0.0.1 или явно заданный isolated test host. 200 независимых cookie sessions выполняют одинаковый mix переходов/чтений в обоих режимах; реальные Tracker вызовы заменены стендовым провайдером. Записать p50/p95, error rate, requests/session, CPU/RAM и настройки throttling. Не запускать на production.
- [ ] Запустить `npm test`, `npm run build`, `npm run lint`, `npm run check:contrast`, всю operational E2E-матрицу и сценарии SW. Отдельно записать предупреждения и непроверенные физические устройства.
- [ ] Визуально осмотреть ПК/телефон и обе темы каждого раздела; независимый reviewer проверяет сравнение classic и полноту нового режима. Исправить подтверждённые регрессии с RED→GREEN, обновить результаты после последнего изменения.
- [ ] Коммит `test(web): verify complete dual-interface parity and performance`. Финальный отчёт не называет этап завершённым при незакрытой строке карты экранов.

## Порядок и метод выполнения

### После приёмки — OTA (добавлено по запросу пользователя)

- [ ] Прочитать действующие release/OTA инструкции проекта и использовать существующий упаковщик, не создавать новый формат архива.
- [ ] Повысить версию по действующим правилам проекта; проверить состав архива: без секретов, dev-зависимостей, локальных прототипов и тестовых пользовательских данных.
- [ ] Проверить манифест, контрольные суммы, применяемую политику подписи и совместимость миграций штатными инструментами; защиту не отключать для обхода ошибки упаковки.
- [ ] На изолированном стенде проверить обновление поверх поддерживаемой версии и возврат в classic после установки. Зафиксировать реально проверенную платформу; не объявлять локальный desktop build проверкой ARM-хоста.
- [ ] Передать архив, SHA-256, версию и краткую инструкцию переключения интерфейса. Указать непроверенные условия и известные ограничения. На рабочий хост самостоятельно не устанавливать.

1 → 2 → 3 → 4 → 5 → 6 → 7 → 8. Классический эталон и общий owner state — обязательная
зависимость всех областей. Этапы 4–7 допускают изолированных исполнителей только
после стабилизации контракта; общие shell/cache файлы у одного владельца.
Для экономии рекомендовано выполнение основным агентом, с одним независимым
финальным ревью. Альтернатива — subagent-driven с отдельным исполнителем и ревью
каждого этапа; это дороже по токенам, но даёт больше независимых проверок.

## Self-review плана

Карта спецификации: shell/public → 1–2; работа/роботы → 3; склад → 4;
репорты/кампании → 5; обзор/парки/аналитика → 6; управление/настройки → 7;
все роли, кэш, PWA и нагрузка → 8 с локальными regression-проверками каждого этапа.
Новый дизайн не получает собственные API-клиенты или permissions policy.
Этот план описывает работу, а не утверждает, что её результаты уже достигнуты.
