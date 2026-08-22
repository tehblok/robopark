# Design: Robopark Phase 2 — operator onboarding & parks

**Date:** 2026-08-22  
**Status:** approved (conversation)  
**Repo:** `robopark` (builds on Phase 1 web skeleton)  
**Prior phase:** `docs/superpowers/specs/2026-08-21-robopark-platform-phase1-design.md`

## Goal

Ship **operator self-registration** gated by a shared password, **admin/royal approval with ≥1 park**, minimal **park CRUD**, and an **operator park-request → admin inbox** loop. No Tracker, Telegram, or mechanic park-password flows in this phase.

## Product decisions (locked)

| Item | Choice |
|------|--------|
| Access gate | Shared password → self-register (username + password) → `pending` → admin/royal **approve** with **≥1 park** (or **reject**) |
| Personal credentials | Chosen at registration by the operator |
| Pending UX | Login allowed; operator cabinet shows **awaiting approval** (no working tools) |
| Reject UX | Account remains; `access_status=rejected`; login shows **rejected**; no self-serve re-apply (admin only) |
| Parks | Minimal CRUD (name + unique tag + active); no Tracker/queue/chat fields |
| Extra parks | Approved operator may request additional parks; admin/royal inbox approve/reject |
| Data model | Approach **A**: `access_status` on `users` + `parks` + `user_parks` + `park_requests` |
| Shared password | Env only: `OPERATOR_SHARED_PASSWORD`; empty/unset → registration disabled |
| Approvers | `admin` and `royal` |

## Non-goals (Phase 2)

- Yandex Tracker / Emergency integrations
- Mechanic park `access_password` onboarding
- Telegram clients
- Operator working tools (reports, blockers, SLA)
- Postgres cutover
- Self-serve re-apply after reject
- Rich RBAC beyond park membership lists

## Actors and behavior

| Actor | Behavior |
|-------|----------|
| Anonymous | Can open `/register` and `/login` |
| Operator `pending` | Can log in; routed to awaiting-approval screen |
| Operator `rejected` | Can log in; routed to rejected screen (no re-register path) |
| Operator `approved` | Operator cabinet; sees assigned parks; can submit park-requests |
| Admin / royal | Park CRUD; access-request inbox; park-request inbox |

Seed `royal` / `admin` users from Phase 1 remain `access_status=approved` with no park requirement for their own cabinets.

## Data model

### `users` (extend Phase 1)

Add:

- `access_status` — enum string: `pending` | `approved` | `rejected`
  - Self-registered operators start as `pending`
  - Seeded admin/royal start as `approved`
  - Existing Phase 1 seed path must set `approved` for non-operator seeds

Unchanged: `username`, `password_hash`, `role`, `is_active`, `created_at`, sessions.

### `parks`

| Column | Notes |
|--------|-------|
| `id` | PK |
| `name` | Display name |
| `tag` | Unique short key (domain tag; Tracker wiring later) |
| `is_active` | Soft-disable; inactive parks cannot be assigned or requested |
| `created_at` | Timestamp |

### `user_parks`

| Column | Notes |
|--------|-------|
| `user_id` | FK → users |
| `park_id` | FK → parks |
| PK | `(user_id, park_id)` |

Approved operators must have **≥1** row after access approve. Admin/royal do not need park rows for Phase 2 cabinet access.

### `park_requests`

| Column | Notes |
|--------|-------|
| `id` | PK |
| `user_id` | Requesting operator |
| `park_id` | Requested park |
| `status` | `pending` \| `approved` \| `rejected` |
| `created_at` | |
| `resolved_at` | Nullable until resolved |
| `resolved_by` | Nullable FK → users (admin/royal) |

Constraints:

- Cannot create a request for a park already in `user_parks`
- Cannot create for inactive park
- At most one `pending` request per `(user_id, park_id)`

### Shared password

- Config/env: `OPERATOR_SHARED_PASSWORD`
- Never stored in DB; never returned by API
- If unset or empty: `POST /auth/register` returns 403 (registration closed)

## Flows

### Registration

1. User opens `/register`, enters shared password + username + password.
2. `POST /auth/register` validates shared password, unique username, password strength policy (same floor as login: non-empty; keep Phase 1 limits unless tightened later).
3. Creates `users` row: `role=operator`, `access_status=pending`, `is_active=true`.
4. Does **not** auto-login (optional later); user proceeds to `/login`.
5. Conflicts: 409 username taken; 403 bad/missing shared password.

### Login routing (SPA)

After `GET /auth/me`:

| Condition | Route |
|-----------|-------|
| Unauthenticated | `/login` |
| `role` in `royal`,`admin` | `/admin` (existing) |
| `role=operator` + `pending` | `/operator/pending` |
| `role=operator` + `rejected` | `/operator/rejected` |
| `role=operator` + `approved` | `/operator` |
| `role=mechanic` | `/mechanic` (unchanged empty shell) |

`/auth/me` payload extends with `access_status` and `parks: [{id, name, tag}]` (empty when none).

### Access approve / reject

Admin/royal inbox lists operators with `access_status` in `pending` (and optionally shows `rejected` for visibility).

- **Approve** `POST /admin/access-requests/{user_id}/approve` body `{ "park_ids": [<id>, ...] }`
  - Requires ≥1 **active** park id
  - Sets `access_status=approved`
  - Upserts `user_parks` for those parks
- **Reject** `POST /admin/access-requests/{user_id}/reject`
  - Sets `access_status=rejected`
  - Does not delete the user
  - No park assignments

### Park CRUD

Admin/royal only:

- `GET /parks` — list (include inactive for admin)
- `POST /parks` — create `{name, tag}`
- `PATCH /parks/{id}` — update name/tag/`is_active`

Operators do not manage parks.

### Park-request loop

Approved operator:

- `GET /operator/parks` — assigned parks
- `GET /operator/park-requests` — own requests (optional but included)
- `POST /operator/park-requests` — `{ "park_id": <id> }`

Admin/royal:

- `GET /admin/park-requests?status=pending` (default pending)
- `POST /admin/park-requests/{id}/approve` — set approved; add `user_parks`; set resolver fields
- `POST /admin/park-requests/{id}/reject` — set rejected; set resolver fields

## Web surfaces (Phase 2)

| Route | Audience | Content |
|-------|----------|---------|
| `/register` | Anonymous | Shared password + username + password form |
| `/login` | Anonymous | Existing + link to register |
| `/operator/pending` | Pending operator | Static “awaiting approval” |
| `/operator/rejected` | Rejected operator | Static “access rejected” |
| `/operator` | Approved operator | Parks list + request-park UI |
| `/admin` | Admin/royal | Nav into parks + access inbox + park-request inbox (can be sections or subroutes) |

No cross-role menus beyond existing Phase 1 rules. Wrong-status operator hitting `/operator` redirects to pending/rejected as appropriate.

## API surface (Phase 2 additions)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/auth/register` | none | Self-register operator |
| GET | `/auth/me` | session | Extended with `access_status`, `parks` |
| GET/POST | `/parks` | admin/royal | List / create |
| PATCH | `/parks/{id}` | admin/royal | Update |
| GET | `/admin/access-requests` | admin/royal | Pending (and optional rejected) operators |
| POST | `/admin/access-requests/{user_id}/approve` | admin/royal | Approve + parks |
| POST | `/admin/access-requests/{user_id}/reject` | admin/royal | Reject |
| GET | `/operator/parks` | approved operator | Own parks |
| GET/POST | `/operator/park-requests` | approved operator | List / create |
| GET | `/admin/park-requests` | admin/royal | Inbox |
| POST | `/admin/park-requests/{id}/approve` | admin/royal | Approve |
| POST | `/admin/park-requests/{id}/reject` | admin/royal | Reject |

Unauthenticated → 401; wrong role → 403; pending/rejected operator calling approved-only routes → 403.

## Security / config

- Shared password compared with constant-time compare where practical; never logged.
- Secrets remain in env / gitignored `*.env`.
- Approve must validate park ids exist and `is_active=true`.
- Registration must not create `admin`/`royal`/`mechanic` roles.

## Success criteria

1. With `OPERATOR_SHARED_PASSWORD` set, register → login → pending screen.
2. Admin/royal approve with ≥1 park → operator reaches `/operator` and sees those parks.
3. Reject → rejected screen; no self-serve re-apply.
4. Admin can create/edit parks; operator can request another park; admin resolves inbox.
5. With shared password unset, registration is closed (403).
6. No dependency on old bot package; Alembic migration applies cleanly on host SQLite.

## Follow-on (not this spec)

- Mechanic park-password onboarding
- Tracker fields on parks / live queue wiring
- Operator reports, blockers, SLA
- Telegram clients with API parity
- Self-serve re-apply or appeal after reject

## Reference only

Old bot (`robopark_tehblokdan`) used shared password without an approve queue and assigned parks manually. Phase 2 **intentionally** adds the approve gate and park-request inbox for the web platform. Domain language (park tags, roles) is reused; code is not.
