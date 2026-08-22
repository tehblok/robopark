# Design: Robopark Phase 3 — mechanic flows (web)

**Date:** 2026-08-22  
**Status:** approved (conversation)  
**Repo:** `robopark` (builds on Phase 1 skeleton + Phase 2 operator onboarding)  
**Prior phases:**
- `docs/superpowers/specs/2026-08-21-robopark-platform-phase1-design.md`
- `docs/superpowers/specs/2026-08-22-robopark-phase2-operator-onboarding-design.md`

## Goal

Ship **mechanic cabinet** with three live tools — **own-park tasks** (Tracker blockers), **robot search** (Tracker, cross-park), and **Emergency check** (VIN → section cards). Admin creates mechanic accounts (username/password + exactly one park, immediately approved) and manages **Tracker token** and **Emergency cookie** via admin UI. Extend parks with Tracker/notify fields.

## Product decisions (locked)

| Item | Choice |
|------|--------|
| Mechanic access | Admin creates `username` + `password` + **exactly 1** `park_id`; no self-register |
| Approval | **Immediately `approved`** — no pending/reject flow for mechanics |
| Login | Same session cookie auth as Phase 1 (`POST /auth/login`) |
| Park binding | Exactly one row in `user_parks`; enforced on create/update |
| Cabinet tools | All three live in Phase 3: tasks · robot search · Emergency |
| Tracker token | Stored in DB; admin sets via **admin UI**; masked in GET responses |
| Emergency cookie | Stored in DB; admin sets via **admin UI**; masked in GET responses |
| Park fields | Extend `parks` with `tracker_queue`, `group_id`, optional `chat_id`, feature flags |
| Emergency field maps | Static JSON in repo (`apps/api/data/emergency_sections.json`), not DB |
| Park password login | **Out of scope** — replaced by admin-created accounts |
| Reference | Old bot UX informs behavior; **no code import** from `robopark_tehblokdan` |

## Non-goals (Phase 3)

- Telegram bots / notify bot keep-alive job (defer keep-alive polling to a follow-on)
- Operator reports, blockers menu, SLA, backlog alerts **for operators**
- Deep ticket work (edit status, comments, assignee changes)
- Mechanic self-registration or park-password activation
- Postgres cutover
- Operator-facing Emergency (mechanic-only sections in Phase 3; operator/admin Emergency later)
- Live deploy to Armbian/VPS (configs/docs yes; SSH when user grants access)

## Actors

| Actor | Behavior |
|-------|----------|
| Admin / royal | Create/manage mechanics; edit park Tracker fields; set Tracker token + Emergency cookie in settings |
| Mechanic | Login → `/mechanic` → tasks / robot search / Emergency |
| Operator | Unchanged from Phase 2 |

## Data model

### `parks` (extend Phase 2)

| Column | Notes |
|--------|-------|
| `tracker_queue` | Yandex Tracker queue key for this park; nullable until configured |
| `group_id` | Telegram group id for notify (future); nullable int |
| `chat_id` | Optional chat id; nullable int |
| `feature_reports` | bool, default true |
| `feature_blockers` | bool, default true — gates mechanic tasks |
| `feature_sla_repair` | bool, default true |
| `feature_backlog_alerts` | bool, default true |

Existing: `id`, `name`, `tag`, `is_active`, `created_at`.

Mechanic **tasks** require: park `is_active`, `feature_blockers=true`, non-empty `tracker_queue`, valid platform Tracker token.

### `users` (mechanic)

- Created by admin: `role=mechanic`, `access_status=approved`, `is_active=true`
- Exactly **one** `user_parks` row (API rejects 0 or >1 parks)

### `platform_settings` (new)

Key-value store (or single-row table) for integration secrets:

| Key | Notes |
|-----|-------|
| `tracker_token` | Yandex Tracker OAuth/token; admin write only |
| `emergency_cookie` | Yandex Team cookie string for Emergency API |
| `*_updated_at` | timestamps per secret |

Rules:
- Never log secret values
- GET for admin returns **masked** values (e.g. last 4 chars) + `updated_at`
- Mechanic/operator endpoints never expose raw secrets

### Static config

- `apps/api/data/emergency_sections.json` — section definitions for mechanic Emergency picker (ported from old bot semantics, new file in repo)
- Optional `apps/api/data/emergency_error_rules.json` — error classification (include if needed for parity)

## External integrations

### Yandex Tracker

- Used for mechanic **tasks** (park queue, filtered by park tag / queue config) and **robot search** (all tickets for robot number/key, **no** park tag filter)
- Token from `platform_settings.tracker_token`
- HTTP client module under `apps/api/src/robopark_api/services/tracker_client.py` (new)
- Cache/blocker loading behavior aligned with old bot: oldest-first for tasks; status filters (All / Movement / Queue / Adjacent / Parts / Other)

### Emergency API

- Upstream: `https://emergency.sdc.yandex-team.ru/api/v1/{VIN}/`
- VIN normalization: same rules as old bot (`YASADR` + padded digits)
- Cookie from `platform_settings.emergency_cookie`
- Client under `apps/api/src/robopark_api/services/emergency_client.py`
- On 401 / HTML passport page → clear error to mechanic + admin-visible “cookie expired” state in settings GET
- Section rendering via `emergency_sections.json` mapper

## Admin flows

### Create / manage mechanics

- `POST /admin/mechanics` — `{ username, password, park_id }` → 201
- `GET /admin/mechanics` — list `{ id, username, park, is_active, created_at }`
- `PATCH /admin/mechanics/{id}` — `{ password?, park_id?, is_active? }` (still exactly one park)

Validation:
- `park_id` must reference active park
- Username unique
- Only admin/royal

### Integration settings UI

Routes under `/admin/settings/integrations` (or nested in existing admin page):

- `GET` — `{ tracker_token_masked, tracker_token_updated_at, emergency_cookie_masked, emergency_cookie_updated_at }`
- `PUT /admin/settings/tracker-token` — `{ token: string }`
- `PUT /admin/settings/emergency-cookie` — `{ cookie: string }`

### Parks CRUD (extend Phase 2)

- `POST/PATCH /parks` accept new Tracker/notify/feature fields
- Admin UI forms for queue, group_id, chat_id, toggles

## Mechanic flows

### Login routing

Unchanged guard: `role=mechanic` + `access_status=approved` → `/mechanic`.

Mechanic without assigned park → `/mechanic/no-park` (static screen) or 403 on tool APIs.

### 1. Tasks (own park)

- `GET /mechanic/tasks?status=<filter>` — returns blocker list for mechanic's park
- Requires park `feature_blockers`, `tracker_queue`, Tracker token configured
- Response: `{ items: [{ id, key, summary, status, robot, created_at, ... }] }` — shape TBD in plan, aligned with Tracker fields used in old bot
- UI: filter tabs + list, read-only (no ticket deep-work)

### 2. Robot search

- `GET /mechanic/robots/{query}/tickets` — `query` = robot number or ticket key
- Returns all matching Tracker issues for that robot **without** park tag filter
- Read-only list/cards

### 3. Emergency check

- `POST /mechanic/emergency/resolve` — `{ robot_number: string }` → `{ vin, sections: [{ id, title }] }`
- `GET /mechanic/emergency/{vin}/sections/{section_id}` — rendered field groups from JSON map
- Any VIN/robot checkable (not limited to mechanic's park), matching old mechanic bot

## Web surfaces

| Route | Audience | Content |
|-------|----------|---------|
| `/mechanic` | Approved mechanic | Hub linking to three tools |
| `/mechanic/tasks` | Mechanic | Blocker list + filters |
| `/mechanic/robot-search` | Mechanic | Search input + results |
| `/mechanic/emergency` | Mechanic | Robot number → section picker → section view |
| `/mechanic/no-park` | Mechanic without park | Static message |
| `/admin` (extend) | Admin/royal | Mechanics CRUD + integration settings + extended park form |

## API surface (Phase 3 additions)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/admin/mechanics` | admin/royal | Create mechanic |
| GET | `/admin/mechanics` | admin/royal | List mechanics |
| PATCH | `/admin/mechanics/{id}` | admin/royal | Update mechanic |
| GET | `/admin/settings/integrations` | admin/royal | Masked secrets status |
| PUT | `/admin/settings/tracker-token` | admin/royal | Set Tracker token |
| PUT | `/admin/settings/emergency-cookie` | admin/royal | Set Emergency cookie |
| PATCH | `/parks/{id}` | admin/royal | Extended fields (also POST create) |
| GET | `/mechanic/tasks` | approved mechanic | Own-park blockers |
| GET | `/mechanic/robots/{query}/tickets` | approved mechanic | Robot ticket search |
| POST | `/mechanic/emergency/resolve` | approved mechanic | Resolve robot → VIN + sections |
| GET | `/mechanic/emergency/{vin}/sections/{section_id}` | approved mechanic | Section card |

Errors: 401 unauthenticated; 403 wrong role or no park; 503 Tracker token missing; 502 upstream Tracker/Emergency failure; 401 Emergency when cookie invalid.

## Security

- Secrets only in DB + admin PUT; never in client bundles or mechanic responses
- Admin masked display only
- Rate-limit or timeout on upstream calls (reasonable defaults in implementation plan)
- Do not commit real tokens/cookies; tests use mocked upstream

## Success criteria

1. Admin creates mechanic with one park → mechanic logs in → sees three tools.
2. Admin sets Tracker token and Emergency cookie in UI; mechanic calls succeed when upstream mocked or live.
3. Tasks show only mechanic's park blockers when park configured; disabled when `feature_blockers=false` or missing queue.
4. Robot search returns cross-park results for a robot query.
5. Emergency flow: robot number → sections → section detail; fails clearly without cookie.
6. Alembic `0003` (or next) applies; full API test suite green; `npm run build` green.
7. No dependency on old bot package code.

## Follow-on (not this spec)

- Emergency keep-alive background job (notify-style polling)
- Operator/admin Emergency and Tracker tools
- Deep ticket actions in web UI
- Telegram thin clients with API parity
- Postgres cutover

## Reference only

Old bot specs (read-only domain reference):

- `robopark_tehblokdan/docs/superpowers/specs/2026-08-13-mechanic-bot-ux-design.md`
- `robopark_tehblokdan/docs/superpowers/specs/2026-08-13-robot-emergency-data-design.md`
- `robopark_tehblokdan/docs/superpowers/specs/2026-08-13-roles-admin-operator-mechanic-design.md`

Web Phase 3 replaces park-password + `mechanic_auths.json` with admin-created users while preserving tool behavior.
