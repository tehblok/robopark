# Robopark — адаптивный shell (ПК + телефон)

**Date:** 2026-08-24  
**Status:** approved for planning  
**Branch (planned):** `feature/mobile-responsive`

## Goal

Один веб-сайт Robopark удобно работает и на ПК, и на телефоне для **всех ролей** (royal/admin/operator/mechanic). На широком экране сохраняется текущий левый сайдбар; на узком — гибрид: bottom bar для частых разделов + бургер «Ещё» для остального.

## Decisions (locked)

| Тема | Решение |
|---|---|
| Scope | Весь продукт (не только механик/оператор) |
| Desktop nav | Левый сайдбар как сейчас |
| Mobile nav | Bottom bar (приоритетные пункты) + sheet «Ещё» |
| Breakpoint | `max-width: 900px` → mobile shell (совпадает с уже существующим dashboard breakpoint) |
| Theme / brand | Без смены визуальной темы; Manrope + orange brand сохраняются |
| Out of scope | PWA, native apps, отдельные mobile-роуты, redesign brand |

## Current state

- `AppShell`: фиксированный `aside.sidebar` + `topbar` (парк, user menu).
- В `index.css` есть слабые media queries (`≤900px`: sidebar сверху сеткой; dashboard KPI), **нет** bottom bar / drawer / safe-area.
- Viewport meta уже есть в `index.html`.
- Навигация из `nav.ts` одинакова для всех ролей (`navItemsForRole` пока не фильтрует).

## Approach

**Dual shell (B):** один `AppShell`, две CSS/DOM-режима по ширине.

```
Desktop (≥901px)          Mobile (≤900px)
┌────────┬──────────┐     ┌──────────────────┐
│Sidebar │ Topbar   │     │ Topbar (park/user)│
│        │ Content  │     │ Content           │
│        │          │     │                   │
│        │          │     ├──────────────────┤
│        │          │     │ Bottom: 4 + Ещё  │
└────────┴──────────┘     └──────────────────┘
```

На mobile сайдбар **скрыт** (не горизонтальная сетка как сейчас).

## Navigation model

### Bottom bar (primary, ≤5 slots)

Порядок и состав (для всех ролей, пока nav общий):

1. Dashboard (`/dashboard`)
2. Tasks (`/tasks`)
3. Emergency (`/emergency`)
4. Reports (`/reports`) — badge count как сейчас
5. **Ещё** — открывает sheet (не route)

Stub-пункты (map / learning / help) **не** в bottom bar.

### Sheet «Ещё»

Содержит:

- Оставшиеся nav items: robot search, analytics, stubs (map/learning/help)
- Admin link (если `admin` / `royal`)
- Theme toggle
- Sign out

Закрытие: backdrop, кнопка закрыть, Escape, после выбора пункта.

### Topbar (mobile)

- Выбор парка (как сейчас; locked → pill)
- User summary открывает тот же sheet «Ещё» **или** остаётся `details` только для user — предпочтительно: user-pill тоже ведёт в sheet, чтобы не плодить меню. Допустимо оставить компактный user `details` + «Ещё» только для nav; **рекомендация:** один sheet «Ещё», user-pill в topbar показывает имя без второго меню.

## Content adaptations (mobile)

| Область | Поведение |
|---|---|
| Forms / `.inline-form` | Колонка, controls `width: 100%`, min touch ~44px |
| `.stat-grid` / dashboard KPI | 2 колонки или 1 на очень узких (`≤480px`) |
| Tracker workspace | Список и деталь стеком; на узком — detail fullscreen с «Назад» к списку |
| Admin lists / long tables | Horizontal scroll контейнер или card rows |
| Panels | Меньше padding; без горизонтального overflow body |
| Login / cabinet | Уже центрированы; подтянуть вертикальные отступы под safe-area |

## Layout / CSS specifics

- CSS variables: `--bottom-nav-height`, `--safe-bottom` (`env(safe-area-inset-bottom)`)
- `.app-content` на mobile: `padding-bottom: calc(var(--bottom-nav-height) + var(--safe-bottom))`
- Touch: убрать hover-lift на coarse pointer (`@media (hover: hover)` для translateY)
- Focus rings сохранить для a11y
- Не ломать desktop классы `.sidebar` / `.sidebar-nav`

## Component changes

| File | Change |
|---|---|
| `AppShell.tsx` | Mobile bottom nav + sheet; hide sidebar via class/CSS; wire badge on Reports |
| `nav.ts` | Helpers: `primaryNavItems()`, `moreNavItems()` (или константы PRIMARY_IDS) |
| `index.css` | Mobile shell, bottom bar, sheet, form/KPI/tracker tweaks |
| Tracker components | Optional selected-issue fullscreen back pattern |
| `i18n/ru.ts` | `nav.more`, `nav.close` |

Без новых роутов. Без API-изменений.

## Acceptance criteria

1. ≥901px: визуально как сейчас (sidebar left), регрессий нет.
2. ≤900px: sidebar не виден; bottom bar с 4 разделами + «Ещё»; контент не перекрыт bar (safe-area).
3. «Ещё» открывает sheet со вторичными пунктами, admin (если есть), theme, logout.
4. Reports badge виден на bottom bar.
5. Dashboard / Tasks / Emergency / Reports / Tracker / Admin просматриваемы без горизонтального скролла страницы (допустим scroll внутри таблицы).
6. Login usable на 375×667.
7. Light/dark theme работают в mobile shell.

## Non-goals

- Role-specific разные наборы bottom bar (можно later, когда `navItemsForRole` начнёт фильтровать)
- Gesture swipe-nav
- Installable PWA
- Переписывание page-логики ради mobile

## Implementation notes

- Предпочтительна одна ветка `feature/mobile-responsive` от `main`.
- Проверка: DevTools iPhone SE / Pixel 5 + desktop 1280.
- Тесты: при наличии RTL/component tests — smoke на наличие bottom bar class; иначе ручной checklist выше.
