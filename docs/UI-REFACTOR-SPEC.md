# Спецификация: доработка UI/UX и системные долги

Документ описывает **оставшуюся** работу. Актуально на момент остановки.
Состояние проверок на этот момент: API — **312** тестов зелёные, `ruff` — чисто,
web — `tsc`, `build`, `npm test` (28) проходят, `oxlint` — **3 warnings, 0 errors**.

**Прогресс:** §1–§7.3, §7.6, §7.5 — сделаны. §7.4 отложено. §8 — частично (см. ниже).

---

## 0. Что уже сделано (контекст, не переделывать)

### Бэкенд
- **ACL fail-closed** — [`tracker_policy.py`](../apps/api/src/robopark_api/services/tracker_policy.py):
  `_check_issue_scope()` требует непустую очередь из списка пользователя **и** тег
  своего парка. Чужой тег — отказ, без тега — только по политике `operator_show_untagged`
  и никогда механику. Разделены `is_issue_in_scope()` (фильтрация списков) и
  `enforce_issue_scope()` (403 для одиночного тикета).
- **Шифрование секретов** — [`crypto.py`](../apps/api/src/robopark_api/crypto.py), Fernet,
  ключ из `SECRET_KEY`, префикс `enc:v1:`, обратная совместимость с plaintext.
- **Брутфорс и пароли** — [`login_throttle.py`](../apps/api/src/robopark_api/services/login_throttle.py),
  `validate_password()` в [`security.py`](../apps/api/src/robopark_api/security.py).
- **Надёжность** — WAL/`busy_timeout`/`foreign_keys` в [`db.py`](../apps/api/src/robopark_api/db.py),
  кэш `get_settings()`, [`session_cleanup.py`](../apps/api/src/robopark_api/services/session_cleanup.py),
  `/health` + `/health/ready`.
- **Аудит** — таблица `audit_log` (миграция `0007`), [`audit.py`](../apps/api/src/robopark_api/services/audit.py),
  роутер [`admin_audit.py`](../apps/api/src/robopark_api/routers/admin_audit.py), подпись автора в комментариях.
- **DTO тикета расширен** — `description`, `assignee`, `reporter`, `priority`, `type`,
  `components`, `updated`; общий маппер [`_blockers.py`](../apps/api/src/robopark_api/routers/_blockers.py).
- **Инфра** — [`ci.yml`](../.github/workflows/ci.yml), Docker non-root + healthcheck, nginx CSP/gzip/кэш.

### Фронтенд
- **Дизайн-система** в [`index.css`](../apps/web/src/index.css): токены `--space-*`,
  `--dur-*`, `--ease-*`, `--shadow-*`, `--warning`/`--info`; кейфреймы
  `fade-in`, `fade-slide-up`, `slide-up-sheet`, `skeleton-shimmer`, `spin`;
  `prefers-reduced-motion`; `:focus-visible`.
- **UI-компоненты**: [`Feedback.tsx`](../apps/web/src/components/ui/Feedback.tsx)
  (`Spinner`, `SkeletonLine/Card/List/Kpi`, `EmptyBlock`),
  [`Tabs.tsx`](../apps/web/src/components/ui/Tabs.tsx) (`Tabs`, `TabPanel`, `Toggle`).
- **PageShell**: проп `actions`, `Panel` с `actions`, `Alert` с тонами
  `error|success|info|warning` и иконкой.
- **Тикеты**: `IssueDetailPanel`, `IssueActionsPanel`, `IssueList`, `IssueFilters`,
  `TrackerWorkspace`, `IssueDrawer`, `TaskBoard`, `issue-utils.ts`.
- **Страницы обновлены**: Dashboard, Reports, Login, Register, RobotSearch,
  Analytics, ComingSoon, Admin (вкладки), MechanicTasks, OperatorBlockers, EmergencyViewer.
- **Навигация**: иконки в `nav.ts` + `AppShell`, активный индикатор, анимация шторки.
- **Адаптив**: планшет 901–1200px, тач-таргеты 44px, safe-area, sticky-футер действий.

---

## 1. Экран AdminEmergencyConfig ✅

**Файл:** [`AdminEmergencyConfig.tsx`](../apps/web/src/pages/AdminEmergencyConfig.tsx)

Сделано:
- `loading` + `<SkeletonList rows={4} />`; загрузка через `useCallback` + `useEffect` (как Admin)
- Каждый раздел — отдельный `Panel` с `actions`: `Badge`, `↑`/`↓` (`btn-ghost`, aria-label), удаление (`btn-danger` + confirm)
- Роли и «Раздел включён» — `Toggle` в `.toggle-list` с `roleLabel`
- Поля — `.form-grid` (Путь / Подпись / действия); «Добавить поле» disabled, пока путь или подпись пусты
- Форма создания — верхний `Panel` с `.form-grid` + `.form-actions`; экспорт JSON в `PageShell.actions`
- `Alert tone="success|error"`; пустое состояние — `EmptyBlock` с `⚑`
- `reloadOnFailure` на reorder сохранён

### Критерий приёмки
`tsc` и `build` проходят; на экране видно скелетон при загрузке; роли переключаются
тумблерами; порядок сохраняется и при ошибке откатывается.

---

## 2. Страницы, которые ещё не приведены к дизайн-системе ✅

Сделано:

| Файл | Решение |
|---|---|
| [`OperatorParks.tsx`](../apps/web/src/pages/OperatorParks.tsx) | Карточки `.park-card`, `.form-grid`, бейджи статусов заявок, `SkeletonList` |
| [`OperatorNowReport.tsx`](../apps/web/src/pages/OperatorNowReport.tsx) | KPI-сетка с тонами, `SkeletonKpi`, `skipped_parks` → `Alert tone="warning"`; встроен в [`Analytics.tsx`](../apps/web/src/pages/Analytics.tsx) для оператора |
| `MechanicIssueWorkspace` / `OperatorIssueWorkspace` | **Удалены**; `/mechanic/tracker` и `/operator/tracker` → `/tasks` |
| [`AdminTrackerWorkspace.tsx`](../apps/web/src/pages/AdminTrackerWorkspace.tsx) | Уже в `PageShell`; политика через `Toggle` |
| [`MechanicNoPark.tsx`](../apps/web/src/pages/MechanicNoPark.tsx), [`NoCabinet.tsx`](../apps/web/src/pages/NoCabinet.tsx), [`OperatorPending.tsx`](../apps/web/src/pages/OperatorPending.tsx), [`OperatorRejected.tsx`](../apps/web/src/pages/OperatorRejected.tsx) | `PageShell standalone` + `EmptyBlock` (`🏭` / `⛔` / `⏳`) + `onLogout` |
| [`Home.tsx`](../apps/web/src/pages/Home.tsx) | Уже редирект на `/login` или `pathForUser` — без лендинга |
| `Mechanic.tsx` / `Operator.tsx` | **Удалены** (не было в роутах) |
| `MechanicRobotSearch` / `OperatorRobotSearch` / `*Emergency` role wrappers | **Удалены**; живой экран — [`Emergency.tsx`](../apps/web/src/pages/Emergency.tsx) + legacy redirects |
| [`Tasks.tsx`](../apps/web/src/pages/Tasks.tsx) | Admin/royal — `EmptyBlock` со ссылками на `/admin/tracker` и `/admin` |

### Критерий приёмки
`tsc` и `build` проходят; `node scripts/check-nav.mjs` ok; мёртвые страницы
не импортируются; статусные экраны единообразны.

---

## 3. Компоненты репортов ✅

**Файлы:** [`ReportList.tsx`](../apps/web/src/components/reports/ReportList.tsx),
[`ReportDetail.tsx`](../apps/web/src/components/reports/ReportDetail.tsx),
[`ReportForms.tsx`](../apps/web/src/components/reports/ReportForms.tsx)

Сделано:
1. `ReportList` — строки в стиле `.issue-row` (акцентная полоса по `kind`,
   бейдж статуса, дата, ключ тикета), `loading` → `SkeletonList`, пусто → `EmptyBlock`,
   опциональный `selectedId`
2. `ReportDetail` — шапка с типом/статусом, поля (парк, автор, тикет), тело,
   комментарий возврата в `Alert tone="warning"`; return/escalate — inline-формы
   без `window.prompt`; кнопки блокируются, пока комментарий пуст
3. `ReportForms` — `.form-grid`, submit disabled пока форма невалидна, `Spinner` на submit,
   ошибки через `mapApiError`
4. CSS: `.report-list`, `.report-detail*`, `.form-grid .field textarea`;
   `ru.reports.actions.cancel`

### Критерий приёмки
`tsc` и `build` проходят; список репортов визуально как тикеты; действия с
обязательным комментарием не отправляются с пустым полем.

---

## 4. Предупреждения линтера ✅

Выбран вариант **B**: правило `react/set-state-in-effect` отключено в
[`.oxlintrc.json`](../apps/web/.oxlintrc.json).

Это осознанный паттерн «загрузить при монтировании»; react-query (вариант A)
отложен — слишком большой рефакторинг (~12 файлов) без явного запроса.

Осталось 3 предупреждения: исправлены в финальном проходе (§8).

---

## 5. Чистка legacy-CSS и мёртвого кода ✅

Сделано:
1. Удалены мёртвые `.tool-grid` / `.tool-card` (хабы механика/оператора удалены в §2);
   `.tracker-item` / `.tracker-panel` в TSX не использовались — убраны из селекторов кнопок
2. Глобальные `button:not(...)` → только классы `.btn*` (chrome-кнопки со своими классами)
3. `EmptyState` удалён из `PageShell`; все экраны на `EmptyBlock` / `SkeletonList`
4. `.inline-form` убран из CSS (формы на `.form-grid` / `.search-form`)
5. Бейджи переведены на `color-mix` + токены `--success`/`--warning`/`--danger`
6. `presetMine` / `presetUnassigned` в i18n оставлены — нужны для §7.1

### Критерий приёмки
`tsc` и `build` проходят; `oxlint` — 16 warnings, 0 errors; CSS меньше (~33.6 kB).

---

## 6. Фронтенд-тесты ✅

Добавлено: `vitest` + `@testing-library/react` + `@testing-library/jest-dom` + `jsdom`.

Файлы:
- [`issue-utils.test.ts`](../apps/web/src/components/tracker/issue-utils.test.ts) —
  `formatAge`, `statusTone`, `priorityTone`, `initials`, `parseTrackerDate` (+0000)
- [`IssueActionsPanel.test.tsx`](../apps/web/src/components/tracker/IssueActionsPanel.test.tsx) —
  ошибка → `alert-error`, кнопки disabled во время запроса
- [`TaskBoard.test.tsx`](../apps/web/src/components/tracker/TaskBoard.test.tsx) —
  рендер карточки, `is-selected`, `onSelect`
- [`Tabs.test.tsx`](../apps/web/src/components/ui/Tabs.test.tsx) —
  `aria-selected`, стрелки, клик
- [`src/test/setup.ts`](../apps/web/src/test/setup.ts) — jest-dom + cleanup между тестами

CI: job `web` → `npm test` (28 passed).

### Критерий приёмки
`npm test` зелёный локально и в CI; `tsc`/`build` не сломаны.

---

## 7. Бэкенд-долги (из первого аудита)

### 7.1 Фильтр «Мои тикеты» ✅

Сделано:
- миграция `0008`: поле `users.tracker_login` (nullable)
- `GET /tracker/issues?assignee=<login|empty>` → QL `Assignee: …` / `Assignee: empty()`
- `UserOut.tracker_login` в `/auth/me`
- админка: Startrek-логин у механика; сохранение через `PATCH /admin/mechanics/{id}`
- UI: пресеты «Мои» / «Без исполнителя» в [`IssueFilters.tsx`](../apps/web/src/components/tracker/IssueFilters.tsx);
  «На себя» в Tracker использует `tracker_login`

### 7.2 Автодополнение исполнителя ✅

Сделано:
- [`tracker_assignees.py`](../apps/api/src/robopark_api/services/tracker_assignees.py) — кандидаты из механиков парка с `tracker_login`
- `GET /tracker/users?q=` → `TrackerUserOut[]`
- UI: debounced `datalist` в [`IssueActionsPanel.tsx`](../apps/web/src/components/tracker/IssueActionsPanel.tsx) через `api.trackerUsers()`

### 7.3 Принудительный сброс пароля ✅

Сделано:
- миграция `0009`: поле `users.must_change_password`
- `POST /auth/change-password` (сбрасывает флаг, сессия сохраняется)
- `UserOut.must_change_password` в `/auth/me`; админка: Toggle у механика
- UI: [`ChangePassword.tsx`](../apps/web/src/pages/ChangePassword.tsx), маршрут `/change-password`,
  редирект из `pathForUser()` и `RequirePasswordChanged` в [`App.tsx`](../apps/web/src/App.tsx)

### 7.4 Персональные токены Tracker (отложено)

Сейчас один служебный OAuth-токен. Атрибуция закрыта через `audit_log` + подпись в комментарии.
Полноценные персональные OAuth — отдельный большой этап.

### 7.5 `ruff format` ✅

Сделано:
- `ruff format .` по `apps/api` (64 файла)
- шаг `ruff format --check` в [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)

### 7.6 Вложения тикета ✅

Сделано:
- `attachments` в DTO тикета ([`tracker_client.py`](../apps/api/src/robopark_api/services/tracker_client.py), `TrackerAttachmentOut`)
- UI: секция «Вложения» в [`IssueDetailPanel.tsx`](../apps/web/src/components/tracker/IssueDetailPanel.tsx)

Чек-листы Startrek — не реализованы (только вложения).

---

## 8. Ручная проверка

### Автоматизировано / smoke (2026-08-26)

- `GET /health` → `200`, `GET /health/ready` → `200` (tracker/emergency «missing» без секретов — ожидаемо)
- Страница `/login` открывается в dev-сборке (заголовок, поля, кнопка «Войти»)
- `alembic upgrade head` на локальной БД применяет миграции `0007`–`0009`
- `node scripts/check-nav.mjs` — все 9 nav-путей есть в `App.tsx`
- `oxlint` — **0 warnings, 0 errors** (исправлены `park-context`, `Reports.tsx`)

### Остаётся вручную перед релизом

1. Поднять локально (после `alembic upgrade head`):
   ```bash
   cd apps/api && .venv/bin/python -m uvicorn robopark_api.main:app --reload --app-dir src
   cd apps/web && npm run dev
   ```
2. Прокликать по ролям: royal → admin → operator → mechanic.
3. **Тёмная тема** на каждом экране (переключатель в меню пользователя).
4. **Реальный телефон**: нижняя навигация, safe-area, sticky-футер тикета, шторка «Ещё».
5. **Docker**: `cd deploy && docker compose build`.
6. **`SECRET_KEY`**: без него — warning в логах; с ним — токен в БД с префиксом `enc:v1:`.

---

## 9. Порядок выполнения (рекомендуемый)

| # | Блок | Объём | Зависимости |
|---|---|---|---|
| 1 | AdminEmergencyConfig (§1) ✅ | средний | — |
| 2 | Компоненты репортов (§3) ✅ | средний | — |
| 3 | Оставшиеся страницы (§2) ✅ | большой | — |
| 4 | Чистка CSS и мёртвого кода (§5) ✅ | средний | — |
| 5 | Фронтенд-тесты (§6) ✅ | средний | — |
| 6 | Линтер: отключить set-state-in-effect (§4) ✅ | малый | — |
| 7 | `tracker_login` + фильтр «Мои» (§7.1) ✅ | большой | — |
| 8 | Автодополнение исполнителя (§7.2) ✅ | большой | — |
| 9 | Смена пароля + вложения (§7.3, §7.6) ✅ | большой | — |
| 9b | `ruff format` (§7.5) ✅ | малый | — |
| 10 | Ручная проверка (§8) | — | smoke ✅; роли/телефон/Docker — вручную |

---

## 10. Команды проверки

```bash
# API
cd apps/api
.venv/bin/ruff check .
.venv/bin/python -m pytest -q          # ожидается 312 passed

# Web
cd apps/web
npx tsc --noEmit -p tsconfig.app.json
npm run build
npm test                                # 28 passed
npm run lint                            # 0 warnings, 0 errors
npm run check-nav
```

**Правило:** не коммитить, если `errors > 0` или тесты падают.
Рост числа warnings относительно 16 — повод разобраться, а не игнорировать.
