# Design: Robopark Phase 4 — operator working tools (web)

**Date:** 2026-08-22  
**Status:** approved (conversation)  
**Repo:** `robopark` (builds on Phase 1 skeleton + Phase 2 operator onboarding + Phase 3 mechanic integrations)  
**Prior phases:**
- `docs/superpowers/specs/2026-08-21-robopark-platform-phase1-design.md`
- `docs/superpowers/specs/2026-08-22-robopark-phase2-operator-onboarding-design.md`
- `docs/superpowers/specs/2026-08-22-robopark-phase3-mechanic-flows-design.md`

## Goal

Give **approved operators** three **read-only** Tracker tools scoped to **assigned parks**: **blockers**, **robot search**, and **«Сейчас по Tracker»** (live park metrics). Reuse Phase 3 Tracker token, client, and status-bucket logic. Restructure operator web UI into a **hub + tool pages** (same pattern as mechanic cabinet).

## Product decisions (locked)

| Item | Choice |
|------|--------|
| Scope | **Core A:** blockers + robot search + now-report only |
| Auth | Same session cookie as Phase 1; `role=operator` + `access_status=approved` |
| Park scope | Only parks in `user_parks` for this operator |
| Blockers | Per selected assigned park; same status filters as mechanic tasks |
| Robot search | Cross-park within operator scope: search **each assigned park's `tracker_queue`**, merge results, dedupe by issue key |
| Now-report | Aggregate metrics across assigned parks with `feature_reports=true`; optional `park_id` filter for single park |
| Navigation | **Hub A:** `/operator` tool grid; park management moves to `/operator/parks` |
| Deep ticket work | **Out of scope** — read-only lists and metrics |
| Tracker token | From `platform_settings.tracker_token` (Phase 3); admin sets in UI |
| Reference | Old bot UX informs behavior; **no code import** from `robopark_tehblokdan` |
| UI language | Russian (`PageShell`, `ru.ts` from PR #4) |

## Non-goals (Phase 4)

- SLA ≥12ч reports, xlsx file reports, untagged blockers inbox
- Mechanic report inbox / operator notifications wiring
- Backlog alert jobs, Telegram notify, keep-alive polling
- Deep ticket actions (status, comments, assignee)
- Operator/admin Emergency
- Postgres cutover
- Alembic migration (no new DB columns in Phase 4)

## Actors

| Actor | Behavior |
|-------|----------|
| Operator `approved` | Hub → blockers / robot search / now-report; parks & park-requests at `/operator/parks` |
| Operator `pending` / `rejected` | Unchanged Phase 2 screens |
| Admin / royal | Unchanged; must configure Tracker token + park `tracker_queue` / feature flags |
| Mechanic | Unchanged Phase 3 |

## Architecture

**Approach A (chosen):** dedicated operator routers mirroring mechanic pattern.

```
apps/api/src/robopark_api/
  deps.py                    # + get_operator_parks, require_operator_park
  routers/
    operator_blockers.py     # GET /operator/blockers
    operator_robots.py       # GET /operator/robots/{query}/tickets
    operator_report.py       # GET /operator/now-report
  services/
    tracker_client.py        # reuse fetch_park_blockers, search_robot_tickets
    tracker_filters.py       # reuse status buckets
    tracker_metrics.py       # NEW: now-report queries + aggregation
```

Shared helpers (`_blocker_out`) may live in a small `tracker_present.py` module if duplication becomes noisy; not required in plan.

## Dependencies (API)

### `get_operator_parks(db, user) -> list[Park]`

- Join `user_parks` → `parks`
- Order by `park.id`
- Used by all three tools for ACL and queue lists

### `require_operator_park(park_id, db, user) -> Park`

- 404 if park not found
- 403 if park not in operator's `user_parks`
- Used by blockers and optional now-report filter

## External integrations

### Yandex Tracker

**Blockers** — same query as mechanic tasks:

- `Queue: {park.tracker_queue}`
- `Priority: blocker`
- Open resolution clause
- `Tags: "{park.tag}"`

**Robot search** — for each distinct `(tracker_queue)` among assigned parks:

- Call existing `search_robot_tickets(token, queue, query)`
- Merge lists; dedupe by `key`; preserve oldest-first order within merged set
- Ticket-key lookup (`ROBO-123`) uses first available queue from assigned parks (same as mechanic single-queue behavior extended to try assigned queues until hit)

**Now-report** — port metric query builders from old bot semantics (new module `tracker_metrics.py`):

| Metric key | Meaning |
|------------|---------|
| `open_blockers` | Open blocker count for park tag |
| `backlog` | Backlog query (no donor tag in Phase 4 — empty donor) |
| `in_transit` | Open issues in relocation / in-transit statuses |
| `queued` | Open issues in queue status bucket |
| `waiting_team` | Waiting for another team |
| `waiting_parts` | Waiting for parts / delivery |
| `arrived` | Created today (park TZ, default `Europe/Moscow`) |
| `done` | Resolved/closed today |

Status key aliases: port `DEFAULT_STATUS_KEYS` equivalents into `tracker_metrics.py` (static dict, not DB).

Per park: skip if `feature_reports=false` or missing `tracker_queue` (include in response `skipped_parks` with reason).

Aggregation:

- `totals` — sum across included parks (dedupe by `(queue, tag)` cache key so duplicate tag across rows isn't double-counted)
- `parks` — list of `{ park_id, park_name, park_tag, metrics }`
- In-memory cache TTL **60s** per cache key `now:{operator_id}:{park_id|all}` (implementation detail)

Sequential Tracker calls per park (no thread pool) — same safety as old bot.

## Operator flows

### 1. Blockers (assigned park)

- `GET /operator/blockers?park_id=<id>&status=<filter>`
- `status` — same values as mechanic: `all`, `moving`, `queued`, `waiting_team`, `waiting_parts`, `other`
- Requires: assigned park, `feature_blockers=true`, non-empty `tracker_queue`, Tracker token
- Response shape mirrors mechanic tasks:

```json
{
  "park_id": 1,
  "park_tag": "Next",
  "status": "all",
  "counts": { "all": 12, "moving": 2, "...": 0 },
  "items": [ { "key", "summary", "status", "robot", "created_at", "hours_created", "url", "bucket" } ]
}
```

- Cap **200** items after filter (same as mechanic)
- Errors: `400` bad status; `403` park not assigned; `409` `blockers_disabled_for_park`; `503` `tracker_token_not_configured`; `502` upstream

### 2. Robot search

- `GET /operator/robots/{query}/tickets`
- No `park_id` — scope is all assigned parks' queues
- Response: reuse `RobotTicketsOut` `{ query, items: [BlockerOut] }`
- Empty assigned parks or no configured queues → `409` `no_tracker_parks`

### 3. Now-report

- `GET /operator/now-report?park_id=<optional>`
- Without `park_id`: all assigned parks eligible for reports
- With `park_id`: single assigned park only
- Response:

```json
{
  "generated_at": "2026-08-22T14:30:00+03:00",
  "scope": "all" | "park",
  "totals": {
    "blocker": 0, "backlog": 0, "in_transit": 0,
    "queued": 0, "waiting_team": 0, "waiting_parts": 0,
    "arrived": 0, "done": 0
  },
  "parks": [
    {
      "park_id": 1,
      "park_name": "Next",
      "park_tag": "Next",
      "metrics": { "open_blockers": 0, "backlog": 0, "...": 0 }
    }
  ],
  "skipped_parks": [
    { "park_id": 2, "park_name": "Sigma", "reason": "reports_disabled" | "no_tracker_queue" }
  ]
}
```

- If no parks produce metrics and none skipped with data → empty totals with explanatory `skipped_parks` or `409` `no_report_parks`

## Web surfaces

| Route | Audience | Content |
|-------|----------|---------|
| `/operator` | Approved operator | Hub: 3 tool cards + link to parks |
| `/operator/parks` | Approved operator | Current Phase 2 content (assigned parks, request park, my requests) |
| `/operator/blockers` | Approved operator | Park selector (assigned only) + status filter tabs + blocker list |
| `/operator/robot-search` | Approved operator | Search input + results (reuse mechanic page patterns) |
| `/operator/now-report` | Approved operator | Refresh button; totals card; per-park breakdown table |
| `/operator/pending` | Pending | Unchanged |
| `/operator/rejected` | Rejected | Unchanged |

### Hub copy (Russian)

| Tool | Title | Description |
|------|-------|-------------|
| Blockers | Блокеры | Открытые blocker по выбранному парку с фильтрами статуса |
| Robot search | Поиск робота | Тикеты по номеру робота или ключу задачи по вашим паркам |
| Now-report | Сейчас по Tracker | Сводка: блокеры, бэклог, статусы, сегодня пришло/сделано |
| Parks link | Мои парки | Заявки на доступ и история запросов |

### UI behavior

- Park selectors only list **assigned** parks; blockers/report hide parks failing feature flags with inline hint
- Loading / error states via `Alert`; API errors through `errors.ts` mapping (`tracker_token_not_configured`, `blockers_disabled_for_park`, etc.)
- Read-only: issue rows link to Tracker URL in new tab
- Reuse `MechanicTasks` / `MechanicRobotSearch` layout patterns; extract shared `BlockerList` / `StatusFilterBar` only if duplication is obvious during implementation

### Routing

- `routes.ts`: approved operator default remains `/operator` (hub, not parks)
- `App.tsx`: add routes; rename `Operator` → `OperatorParks` at `/operator/parks`

## API surface (Phase 4 additions)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/operator/blockers` | approved operator | Park blockers + status filter |
| GET | `/operator/robots/{query}/tickets` | approved operator | Multi-queue robot search |
| GET | `/operator/now-report` | approved operator | Live metrics snapshot |

Existing `/operator/parks`, `/operator/available-parks`, `/operator/park-requests` unchanged.

## Schemas (new / extended)

| Model | Fields |
|-------|--------|
| `OperatorBlockersOut` | `park_id`, `park_tag`, `status`, `counts`, `items: list[BlockerOut]` |
| `ParkMetricsOut` | `park_id`, `park_name`, `park_tag`, `metrics: dict[str, int]` |
| `SkippedParkOut` | `park_id`, `park_name`, `reason: str` |
| `NowReportOut` | `generated_at`, `scope`, `totals`, `parks`, `skipped_parks` |

Reuse `BlockerOut`, `RobotTicketsOut` from Phase 3.

## Security

- Operator cannot pass arbitrary `park_id` — always validated against `user_parks`
- Secrets never exposed to operator endpoints
- Tracker calls time out at 30s (existing client default)
- Tests mock Tracker HTTP; no real tokens in repo

## Success criteria

1. Approved operator with ≥1 assigned configured park sees hub with three tools.
2. Blockers list matches mechanic task semantics for the same park/tag/queue.
3. Robot search returns merged results across operator's park queues; dedupes keys.
4. Now-report shows totals + per-park rows; respects `feature_reports`; caches ~60s.
5. `/operator/parks` preserves Phase 2 park-request flows.
6. Russian UI on all new/ moved pages.
7. API tests cover ACL (wrong park → 403), feature flags, token missing, mocked upstream; full suite green; `npm run build` green.
8. No Alembic migration required; no import from old bot package.

## Follow-on (not this spec)

- Phase 4B: SLA ≥12ч, untagged blockers, mechanic report inbox
- Operator/admin Emergency
- Deep ticket work in web UI
- Background jobs (backlog alerts, SLA monitor)
- Postgres cutover

## Reference only

- `robopark_tehblokdan/docs/superpowers/specs/2026-08-13-roles-admin-operator-mechanic-design.md`
- Old bot: `handlers/blockers.py`, `handlers/reports.py`, `services/now_report.py`, `tracker.py` (query builders)
