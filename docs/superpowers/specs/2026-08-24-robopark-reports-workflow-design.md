# Robopark — Workflow репортов (механик → оператор → админ)

**Date:** 2026-08-24  
**Status:** implemented  
**Depends on:** UI shell + dashboard (`feature/ui-shell-dashboard` / merged main), Tracker tasks UI, park membership  
**Supersedes stub:** `Reports.tsx` каркас из UI redesign; KPI Tracker остаётся в Дашборде / now-report, не в «Репортах»

## Goal

Реализовать человеческую цепочку репортов в приложении: механик создаёт вопросы/проблемы и при закрытии тикета автоматически шлёт оператору запрос на проверку; оператор разбирает inbox (вернуть / готово / закрыть просмотр) и может эскалировать админу; админ разбирает эскалации. Уведомления в v1 — только in-app (бейдж).

## Non-goals (v1)

- Telegram / push
- Вложения (фото, файлы)
- Отдельные «админские» репорты без эскалации от оператора
- SLA ≥12h, xlsx file reports (Phase 4B backlog)
- Webhook от Yandex Tracker как единственный источник close-событий (источник — наша кнопка «Закрыть»)
- Углублённая аналитика / карта / обучение

## Decisions

| Topic | Choice |
|-------|--------|
| Model | Одна сущность `Report` + `kind` (подход A) |
| Mechanic kinds | `ticket_question`, `ticket_close_review`, `mechanic_problem` |
| Admin path | Только `escalation_to_admin` от оператора |
| Ticket link | Обязателен для `ticket_question` и `ticket_close_review`; опционален для `mechanic_problem` |
| Auto create | Только при «Закрыть» тикет в нашем UI → `ticket_close_review` |
| Manual create | Вопрос и Проблема — отдельные формы |
| Receiver actions | Вернуть (+ comment), Готово (`done`), Закрыть (UI only) |
| Done semantics | Статус `done`, не hard-delete (аудит) |
| Notifications | In-app badge only |
| Approach | Waves: API → Reports UI → Tasks integration |

## Kinds

| kind | Кто создаёт | Target | Tracker key |
|------|-------------|--------|-------------|
| `ticket_question` | Механик (вручную) | operator | required |
| `ticket_close_review` | Система при «Закрыть» | operator | required |
| `mechanic_problem` | Механик (вручную) | operator | optional |
| `escalation_to_admin` | Оператор | admin | optional (копия/ссылка на parent) |

## Statuses

| status | Meaning |
|--------|---------|
| `open` | В inbox получателя |
| `returned` | Вернули автору; для close_review тикет снова в задачах механика + `return_comment` |
| `done` | Закрыт получателем («Готово»); скрыт из активного inbox |

## Data model (logical)

`reports`:

- `id`, `kind`, `status`
- `park_id` (FK)
- `author_user_id` (FK)
- `target_role` (`operator` | `admin`)
- `tracker_key` (nullable string), `tracker_url` (nullable)
- `title`, `body`
- `parent_report_id` (nullable FK, for escalations)
- `return_comment` (nullable)
- `created_at`, `updated_at`, `resolved_at` (nullable)

Indexes: `(target_role, status, park_id)`, `(author_user_id, created_at)`, `(tracker_key)` where not null.

## UX by role

### Mechanic

- `/reports`: свои репорты + статусы; формы «Вопрос по тикету», «Проблема»
- В задачах/Tracker UI: «Закрыть» → создаёт `ticket_close_review` для операторов парка
- При `returned`: комментарий виден; задача снова в `/tasks`

### Operator

- Inbox `open` по паркам оператора
- Карточка: текст, ссылка Tracker, автор, парк
- **Вернуть** / **Готово** / **Закрыть** (выйти без смены статуса)
- **Эскалировать админу** → `escalation_to_admin`
- Опционально вкладка исходящих эскалаций

### Admin / royal

- Inbox эскалаций `open`
- Те же Вернуть / Готово / Закрыть

### Badge

- Сайдбар «Репорты»: count `open` для текущей роли-получателя (и mechanic может видеть count `returned` — optional; v1: badge = open for operator/admin, for mechanic show returned count or combined — **mechanic badge = count of `returned` on own reports**; operator/admin = `open` inbox)

## API

| Method | Path | Role | Notes |
|--------|------|------|-------|
| POST | `/reports` | mechanic | body: kind in {ticket_question, mechanic_problem}, park, title, body, tracker_* |
| POST | `/reports/from-ticket-close` | mechanic | tracker_key required; creates ticket_close_review |
| GET | `/reports/inbox` | operator, admin | open for target_role; park filter |
| GET | `/reports/mine` | all creators | author's reports |
| GET | `/reports/{id}` | participants | author, target park members, admin |
| POST | `/reports/{id}/return` | operator, admin | `{ comment }` required |
| POST | `/reports/{id}/done` | operator, admin | → done |
| POST | `/reports/{id}/escalate` | operator | → new escalation_to_admin |
| GET | `/reports/badge` | authenticated | counts for badge |

Authz: park membership for mechanic/operator; admin/royal for admin inbox; 403 cross-park.

## Ticket close integration

1. Mechanic presses «Закрыть» in our tasks/tracker issue UI.
2. Backend may transition Tracker issue (existing tracker actions) **and** creates `Report(kind=ticket_close_review, status=open)`.
3. Operators of that park see it in inbox with Tracker link.
4. **Вернуть:** status `returned`, comment stored; mechanic sees it in tasks/reports; issue remains/reopens per Tracker policy already used for mechanic workflow (document exact Tracker transition in plan — prefer reuse existing reopen/return action if any).
5. **Готово:** status `done`; removed from active inbox.

No dependency on Tracker webhooks in v1.

## Architecture

```
Mechanic UI (tasks close / forms)
  → POST /reports | /reports/from-ticket-close
  → reports table

Operator / Admin UI (/reports)
  → inbox | return | done | escalate
  → badge count

Dashboard / now-report  (unchanged KPI path)
```

## Implementation waves

1. **Model + API** — migration, services, router, authz tests  
2. **Reports UI** — replace stub, inbox/mine/detail, forms, badge  
3. **Tasks integration** — close button → from-ticket-close; returned surface in tasks  

## Error handling

- Validation errors on missing tracker_key for kinds that require it → 400  
- Duplicate open `ticket_close_review` for same tracker_key+park: reject or return existing (prefer **return existing open** to avoid spam)  
- Tracker transition failure: do not leave orphan report without clear error — either transactional best-effort (report only if Tracker OK) or report+flag; **prefer:** create report only after successful Tracker close call, else 502  

## Testing

- Unit/API: kind validation, authz matrix, return/done/escalate, badge counts, duplicate close  
- Web: mechanic forms, operator inbox actions, badge, close from tasks  
- Manual: full path close → operator return → mechanic sees comment  

## Open questions (non-blocking for plan)

1. Exact Tracker status transition on mechanic «Закрыть» and on operator «Вернуть» (reuse existing action codes).  
2. Should mechanic badge include only `returned`, or also unanswered questions? (v1: returned only.)  

## Approval

- Approach A: approved  
- Section 1 model/statuses: approved  
- Section 2 UX: approved  
- Section 3 API + close: approved  
- Section 4 waves: approved  
