# Robopark — UI shell, тема, дашборд и навигация

**Date:** 2026-08-24  
**Status:** implemented  
**Depends on:** Phase 4/5 Tracker (now-report, blockers, park queue/tag), Phase 6 Emergency (shared viewer)  
**Refs:** эскиз «Робопарк Сервис» (layout), скрин soft UI (светлая палитра + оранжевый акцент)

## Goal

Полный визуальный и навигационный редизайн веб-приложения под эскиз и скрин: общий shell с сайдбаром на русском, светлая/тёмная тема, все существующие экраны в новой стилистике, дашборд парка на данных Tracker с историей приходов/уходов блокеров за неделю (обновление раз в 2 часа). Недостающие разделы меню — заглушки «Скоро»; цепочка репортов и прочее, не вошедшее в этот проход, — в бэклоге следующего прохода.

## Non-goals (этот проход)

См. также полный список в **§ Next pass backlog**. Кратко:

- Полноценный workflow репортов механик → оператор → админ (только каркас UI + контракт).
- Живая карта / телеметрия / глифы роботов.
- Контент Обучение и Помощь.
- Глубокая аналитика сверх графика истории блокеров и существующих метрик now-report.
- Отдельные мобильные приложения / Telegram parity.

## Decisions

| Topic | Choice |
|-------|--------|
| Scope первого UI-эпика | Полный редизайн всех экранов (вариант 3), волнами |
| Навигация | Один общий shell; пункты сайдбара фильтруются по роли |
| Недостающие пункты меню | Видимые заглушки «Скоро» |
| Тема | Light + Dark, переключатель; дефолт light; `localStorage` |
| Визуал light | Как на скрине: фон `#F5F5F5`, белые карточки, оранжевый акцент, крупный radius |
| Визуал dark | Та же компоновка/радиусы; уголь/графит; тот же оранжевый акцент |
| Бренд | «Робопарк Сервис» |
| Язык UI | Только русский |
| Дашборд data | Tracker; контекст = выбранный Парк |
| Park Tracker filter | `tracker_queue` + `tag`; опционально `tracker_priority` (default `blocker`), `tracker_type` (пусто = без фильтра) |
| График | Приходы и уходы блокеров за **7 дней**, две серии |
| История | Гибрид: job каждые **2 часа** сканит Tracker → пишет в БД **per-park**; UI читает только локальную историю |
| Масштаб истории | Индекс `(park_id, bucket_start)`; батч парков с лимитом параллелизма; retention сырых бакетов 14–30 дней |
| Аналитика / Репорты | Пункты меню для **всех** ролей; разный смысл по роли (см. ниже) |
| Репорты в этом проходе | Каркас экранов + контракт ролей; полная бизнес-логика — next pass |
| Старые URL | Редиректы `/operator/*`, `/mechanic/*`, `/admin` tool paths → новые |
| Подход реализации | Волны C: shell → restyle+stubs → dashboard+history |

## Architecture

```
Browser
  AppShell (sidebar + header: Парк | Пользователь | theme)
    → role-filtered nav
    → pages (restyled existing + stubs + dashboard)

Dashboard
  → GET /dashboard/summary?park_id=     (now-report-like + moving list)
  → GET /dashboard/history?park_id=&days=7

HistoryJob (every 2h)
  → for each active park with queue+tag
  → Tracker scan (arrived/departed window since last bucket)
  → UPSERT park_blocker_history

Reports (this pass: shell only)
  → /reports  (role-specific placeholder + API contract sketch)
```

### Components

| Piece | Responsibility |
|-------|----------------|
| CSS tokens (`data-theme`) | Light/dark palette, radius, type |
| `AppShell` | Sidebar, header, park switcher, user menu, theme toggle |
| Nav config | Role → visible items; stub vs live route |
| Page restyle | Existing tools under new chrome |
| Stub pages | Map, Learning, Help; Analytics/Reports каркас |
| `park_blocker_history` + Alembic | Per-park time buckets |
| History job | 2h Tracker scan → DB |
| Dashboard API | Summary + history for chart/widgets |
| Optional park fields | `tracker_priority`, `tracker_type` |

## Visual system

- **Light:** background `#F5F5F5` (or equivalent), cards `#FFFFFF`, text charcoal, accent orange (logo, badges, primary CTA), status green for OK meters.
- **Dark:** deep charcoal background, elevated cards slightly lighter, light text, **same orange accent**.
- **Geometry:** high border-radius (pill inputs, ~12–16px cards), outline icons, soft/minimal shadows.
- **Typography:** expressive sans (keep Manrope or similar; avoid Inter/Roboto/Arial as default stack).
- **Theme toggle:** in User menu or header; persist `localStorage`; apply via `data-theme` on root.

## Navigation

Один сайдбар; подписи на русском.

| Пункт | Route | Roles | This pass |
|-------|-------|-------|-----------|
| Дашборд | `/dashboard` | mechanic (свой парк), operator, admin/royal | Live (wave 3) |
| Задачи | `/tasks` | all (mechanic→tasks, operator→blockers, admin→tracker/сводка) | Restyle existing |
| Поиск по роботу | `/robots/search` | all viewers | Restyle existing |
| Проверка по роботу | `/emergency` | mechanic, operator, admin/royal | Restyle Emergency |
| Карта | `/map` | all | Stub «Скоро» |
| Аналитика | `/analytics` | all | Каркас; смысл по роли |
| Репорты | `/reports` | all | Каркас цепочки; полная логика — next pass |
| Обучение | `/learning` | all | Stub «Скоро» |
| Помощь | `/help` | all | Stub «Скоро» |

**Header:**

- **Парк** — контекст дашборда/аналитики (operator: выбор среди парков; mechanic: один парк, read-only; admin: любой).
- **Пользователь** — роль, тема, выход; **Администрирование** (admin/royal) → парки, токены, Emergency config, tracker policy.

**Home:** `/` → `/dashboard` (если роль допускает) иначе первый доступный пункт.

**Legacy redirects:** preserve bookmarks from `/operator/...`, `/mechanic/...`, `/admin/tracker`, etc.

### Analytics & Reports (product meaning)

| Role | Репорты | Аналитика |
|------|---------|-----------|
| Mechanic | Отправка репорта **оператору** | Метрики своего парка / смены |
| Operator | Разбор репортов **механиков** + отправка репортов **админу** | Парк(и): Tracker + цепочка |
| Admin/royal | Разбор репортов **операторов** | Сводка по паркам |

Цепочка: **механик → оператор → админ**.

В **этом** проходе: экраны в меню, пустые/каркасные состояния, черновик API в spec/plan. Реализация хранения, статусов, уведомлений — **next pass**.

Текущий `GET /operator/now-report` остаётся источником KPI и может жить как виджет дашборда / раздел аналитики, не заменяя «Репорты».

## Dashboard

### Widgets (эскиз)

1. **Chart** — two series, last 7 days: blocker arrivals + departures (from local history).
2. **Перемещение** — list of issues in moving/in_transit (existing blockers/Tracker list APIs).
3. **Пришли / Ушли / В очереди** — counts from now-report semantics (`arrived` / `done` / `queued`).

### Park Tracker scoping

- Required today: `Park.tracker_queue`, `Park.tag`.
- Add optional: `tracker_priority` (default `"blocker"`), `tracker_type` (nullable; omit from QL if empty).
- Skip parks without queue+tag in history job (same spirit as now-report skipped parks).

### History storage (growth-ready)

Suggested table `park_blocker_history`:

| Column | Notes |
|--------|-------|
| `id` | PK |
| `park_id` | FK, indexed |
| `bucket_start` | UTC datetime, start of 2h bucket |
| `arrived_count` | int |
| `departed_count` | int |
| `scanned_at` | when job wrote the row |
| Unique | `(park_id, bucket_start)` |

- Job interval: **2 hours**.
- Source: Tracker scan per park (reuse/extend arrived/done query ideas; window = bucket).
- Read API: `GET /dashboard/history?park_id=&days=7` → series for chart.
- Concurrency: process parks in batches with capped parallelism.
- Retention: keep 14–30 days of buckets (config); chart uses 7; archive/partition later without schema rewrite.
- Authz: mechanic = own park only; operator = assigned parks; admin/royal = any.

### Chart empty states

- No history yet (job not run): empty chart + hint «данные появятся после первого скана».
- Park not configured: message to configure queue/tag (admin).

## Implementation waves

### Wave 1 — Shell

- Tokens light/dark + theme toggle.
- `AppShell` (sidebar, header Парк/Пользователь).
- Role nav config + stub routes registered.
- Legacy redirects; wrap existing pages without full visual polish.

### Wave 2 — Full restyle + stubs

- Restyle all existing screens (auth, tasks/blockers, search, emergency, tracker workspaces, admin).
- Stub pages: Map, Learning, Help.
- Analytics & Reports: role-aware shells + empty states; document API contract for next pass.
- Russian copy consistent; prefer `i18n/ru.ts` for shell strings.

### Wave 3 — Dashboard + history

- Optional park fields priority/type + admin UI.
- Migration `park_blocker_history`.
- Background job every 2h.
- Dashboard page wired to summary + history APIs.
- Tests: job upsert, authz, chart series shape, theme persistence smoke.

## Error handling

- Tracker/history failures: per-park error on dashboard widgets; do not fail whole shell.
- Unauthorized park_id: 403.
- Theme: invalid stored value → fall back to light.

## Testing

- Unit: history upsert idempotency; role nav visibility matrix.
- API: dashboard history authz; job writes buckets for configured parks only.
- Web: shell renders per role; theme toggle persists; stubs reachable; legacy redirects.
- Manual: light/dark vs screenshot feel; dashboard with real park after job run.

## Next pass backlog

Всё, что **осознанно не входит** в текущий эпик — делать следующим проходом (отдельный spec/plan):

### Репорты (полный workflow)

- Модель заявок/репортов: автор, роль, целевая роль, парк, статус, текст, вложения, timestamps.
- Механик: создание и отправка репорта оператору; статусы «черновик / отправлен / принят / отклонён / нужен ответ».
- Оператор: очередь входящих от механиков; разбор, комментарии, эскалация; создание репорта админу.
- Админ: очередь входящих от операторов; разбор, резолюции.
- Уведомления (in-app / позже Telegram).
- Права и аудит; фильтры по парку и статусу.
- Связь репорта с роботом / тикетом Tracker (если нужно).

### Аналитика (углубление)

- Ролевые дашборды аналитики сверх графика истории блокеров.
- Сравнение парков, SLA, тренды длиннее 7 дней.
- Экспорт отчётов (CSV/Excel).
- Drill-down списки «пришли/ушли за день» (сейчас только counts в now-report).

### Карта

- Карта парка / роботов, позиции, статусы.
- Интеграция с телеметрией / Emergency position (если появится продукт-решение).
- Без дублирования non-goals Phase 6 map UI, пока нет отдельного продукта.

### Обучение и Помощь

- Контент: статьи, чеклисты, видео/ссылки.
- Контекстная помощь по экранам.
- Возможно CMS или markdown в репо.

### Tracker / парки

- Если priority/type недостаточно — полноценные policy profiles на парк.
- Donor-tag и сложные QL-исключения в UI.
- Отдельный history archive / cold storage при большом числе парков.

### UX / платформа

- Адаптив mobile-first полировка сайдбара (drawer).
- Кастомизация порядка пунктов меню пользователем.
- Онбординг-тур по новому shell.
- A11y audit (контраст dark/light, focus).

### Техдолг рядом с редизайном

- Убрать дубли ручек mechanic/operator после стабилизации новых путей.
- Вычистить хардкод русских строк в pages → `i18n/ru.ts`.
- Единый empty/error pattern для всех tool pages.

## Open questions (next pass, не блокируют этот)

1. Формат репорта механика: свободный текст vs форма с полями (VIN, тип неисправности)?
2. Нужны ли уведомления в Telegram в первой версии цепочки репортов?
3. Карта: чей источник координат (Emergency vs другой сервис)?

## Approval

- Approach C (волны): approved  
- Section 1 visual + shell + theme: approved  
- Section 2 nav (+ analytics/reports roles): approved  
- Section 3 dashboard + history: approved  
- Section 4 waves + next-pass capture: approved  
