# Phase 3 Mechanic Flows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship mechanic cabinet with three live tools (own-park tasks, cross-park robot search, Emergency VIN check), admin-created mechanic accounts (exactly one park, immediately approved), admin UI for Tracker token and Emergency cookie, and extended park Tracker/notify fields.

**Architecture:** Extend `parks` with Tracker fields; add `platform_settings` key-value store for integration secrets; new service modules for Tracker/Emergency HTTP clients (mocked in tests); admin routes for mechanics + settings; mechanic routes gated by `require_approved_mechanic` + single-park helper; static `emergency_sections.json` for section rendering.

**Tech Stack:** Phase 2 stack + `httpx` (main dependency) for upstream calls; pytest with `unittest.mock`/`respx`-free httpx patching; React pages under `/mechanic/*`.

**Origin:** Approved design `docs/superpowers/specs/2026-08-22-robopark-phase3-mechanic-flows-design.md`.

**Base branch:** `origin/main` (Phase 2 merged). Use worktree `.worktrees/phase3-mechanic` on branch `feature/phase3-mechanic`. Rebase local doc-only commits onto `origin/main` before coding if local `main` is behind.

## Global Constraints

- Mechanics: admin-created only; `role=mechanic`, `access_status=approved`, exactly **one** `user_parks` row.
- No mechanic pending/reject flow; no self-register for mechanics.
- Secrets (`tracker_token`, `emergency_cookie`) live in DB only; admin GET returns masked values; never log or return raw secrets to mechanics/operators.
- Tasks require: mechanic park `is_active`, `feature_blockers=true`, non-empty `tracker_queue`, valid platform Tracker token.
- Robot search and Emergency are **cross-park** (any robot/VIN), matching old mechanic bot.
- Do **not** import code from `robopark_tehblokdan`; port behavior only.
- Out of scope: Telegram, keep-alive job, deep ticket edits, operator Tracker/Emergency tools, Postgres cutover.
- Follow Phase 2 patterns: hermetic `conftest`, `require_admin`, cookie auth, minimal web UI.

## File map

```
apps/api/src/robopark_api/
  models.py                    # Park extended cols; PlatformSetting
  schemas.py                   # mechanic/admin/settings DTOs
  deps.py                      # require_approved_mechanic, get_mechanic_park
  services/
    platform_settings.py       # get/set/mask secrets
    tracker_client.py          # httpx Tracker API
    tracker_filters.py         # status buckets (port inline_park logic)
    emergency_vin.py           # normalize_robot_id
    emergency_sections.py      # JSON map loader + render
    emergency_client.py        # httpx Emergency API
  routers/
    admin_mechanics.py         # NEW
    admin_settings.py          # NEW
    mechanic_tasks.py          # NEW
    mechanic_robots.py         # NEW
    mechanic_emergency.py      # NEW
    parks.py                   # extend create/patch/out schemas
  main.py                      # include new routers
apps/api/data/
  emergency_sections.json      # ported from old bot semantics
  emergency_error_rules.json   # optional; include if errors section needs classify
apps/api/alembic/versions/0003_phase3_mechanic_integrations.py
apps/api/tests/
  test_phase3_models.py
  test_platform_settings.py
  test_admin_mechanics.py
  test_parks_extended.py
  test_mechanic_tasks.py
  test_mechanic_robots.py
  test_mechanic_emergency.py
  test_emergency_sections.py
  fixtures/tracker_issues.json
  fixtures/emergency_robot.json
apps/web/src/
  api.ts, routes.ts, App.tsx
  pages/Mechanic.tsx            # hub
  pages/MechanicTasks.tsx
  pages/MechanicRobotSearch.tsx
  pages/MechanicEmergency.tsx
  pages/MechanicNoPark.tsx
  pages/Admin.tsx               # mechanics + settings + extended park form
.env.example, README.md, deploy/host.env.example
```

---

### Task 1: Models + Alembic `0003`

**Files:**
- Modify: `apps/api/src/robopark_api/models.py`
- Create: `apps/api/alembic/versions/0003_phase3_mechanic_integrations.py`
- Create: `apps/api/tests/test_phase3_models.py`

**Interfaces:**
- Extend `Park`:
  - `tracker_queue: str | None`
  - `group_id: int | None`
  - `chat_id: int | None`
  - `feature_reports: bool` default True
  - `feature_blockers: bool` default True
  - `feature_sla_repair: bool` default True
  - `feature_backlog_alerts: bool` default True
- Add `PlatformSetting`:
  - `key: str` PK (max 64)
  - `value: str` (Text)
  - `updated_at: datetime` server default now, onupdate now

- [ ] **Step 1: Write failing tests**

```python
# apps/api/tests/test_phase3_models.py
from robopark_api.models import Base, Park, PlatformSetting


def test_metadata_has_platform_settings():
    assert "platform_settings" in Base.metadata.tables


def test_park_has_tracker_fields():
    cols = {c.name for c in Park.__table__.columns}
    assert {"tracker_queue", "group_id", "chat_id", "feature_blockers"} <= cols


def test_platform_setting_columns():
    cols = {c.name for c in PlatformSetting.__table__.columns}
    assert {"key", "value", "updated_at"} <= cols
```

- [ ] **Step 2: Run — expect FAIL**

Run: `cd apps/api && source .venv/bin/activate && pytest tests/test_phase3_models.py -v`  
Expected: FAIL

- [ ] **Step 3: Implement models + migration**

Add columns with server defaults for booleans; migration backfills existing parks with defaults. Create empty `platform_settings` table.

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit** `feat(api): add Phase 3 park fields and platform_settings`

---

### Task 2: Platform settings service + admin integration routes

**Files:**
- Create: `apps/api/src/robopark_api/services/platform_settings.py`
- Create: `apps/api/src/robopark_api/routers/admin_settings.py`
- Modify: `apps/api/src/robopark_api/schemas.py`
- Modify: `apps/api/src/robopark_api/main.py`
- Create: `apps/api/tests/test_platform_settings.py`

**Keys (constants):**
- `TRACKER_TOKEN_KEY = "tracker_token"`
- `EMERGENCY_COOKIE_KEY = "emergency_cookie"`

**Service API:**

```python
def mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 4:
        return "****"
    return "*" * (len(value) - 4) + value[-4:]

def get_setting(db, key: str) -> PlatformSetting | None: ...
def set_setting(db, key: str, value: str) -> PlatformSetting: ...
def get_tracker_token(db) -> str | None: ...
def get_emergency_cookie(db) -> str | None: ...
```

**Routes (`/admin/settings`, admin/royal only):**

| Method | Path | Body | Response |
|--------|------|------|----------|
| GET | `/admin/settings/integrations` | — | `{ tracker_token_masked, tracker_token_updated_at, emergency_cookie_masked, emergency_cookie_updated_at, emergency_cookie_valid: bool \| null }` |
| PUT | `/admin/settings/tracker-token` | `{ token: str }` | masked GET shape |
| PUT | `/admin/settings/emergency-cookie` | `{ cookie: str }` | masked GET shape |

`emergency_cookie_valid`: set `false` when last Emergency upstream call returned 401/HTML passport; `true` after successful fetch; `null` if never tested.

- [ ] **Step 1: Tests** — set token, GET masked (`****` + last 4), raw never in JSON; empty GET returns nulls.

- [ ] **Step 2: Implement service + router**

- [ ] **Step 3: `pytest tests/test_platform_settings.py -v` PASS**

- [ ] **Step 4: Commit** `feat(api): admin integration settings with masked secrets`

---

### Task 3: Extend parks CRUD

**Files:**
- Modify: `apps/api/src/robopark_api/routers/parks.py`
- Modify: `apps/api/src/robopark_api/schemas.py` (or inline ParkOut in router — match Phase 2 style)
- Create: `apps/api/tests/test_parks_extended.py`

**Extended `ParkOut` / create / update:**

```python
class ParkOut(BaseModel):
    id: int
    name: str
    tag: str
    is_active: bool
    tracker_queue: str | None
    group_id: int | None
    chat_id: int | None
    feature_reports: bool
    feature_blockers: bool
    feature_sla_repair: bool
    feature_backlog_alerts: bool
```

All new fields optional on PATCH; defaults on POST create.

- [ ] **Step 1: Tests** — create park with `tracker_queue="ROBOPARK"`, patch `feature_blockers=false`, list returns new fields.

- [ ] **Step 2: Implement schema + router changes**

- [ ] **Step 3: `pytest tests/test_parks_extended.py tests/test_parks.py -v` PASS**

- [ ] **Step 4: Commit** `feat(api): extend park CRUD with Tracker and feature flags`

---

### Task 4: Admin mechanics CRUD

**Files:**
- Create: `apps/api/src/robopark_api/routers/admin_mechanics.py`
- Modify: `apps/api/src/robopark_api/schemas.py`
- Modify: `apps/api/src/robopark_api/main.py`
- Create: `apps/api/tests/test_admin_mechanics.py`

**Routes:**

| Method | Path | Body | Notes |
|--------|------|------|-------|
| POST | `/admin/mechanics` | `{ username, password, park_id }` | 201; creates mechanic approved + one UserPark |
| GET | `/admin/mechanics` | — | list with embedded park summary |
| PATCH | `/admin/mechanics/{id}` | `{ password?, park_id?, is_active? }` | still exactly one park after patch |

**Validation:**
- Username unique (409)
- `park_id` must exist and `is_active` (400)
- On create: delete/replace any existing user_parks (should be none)
- On patch `park_id`: replace single row, never 0 or 2 parks
- Role always `mechanic`; `access_status=approved`

**Response shape:**

```python
class MechanicOut(BaseModel):
    id: int
    username: str
    is_active: bool
    created_at: datetime
    park: ParkOut  # single park
```

- [ ] **Step 1: Tests** — create, list, patch password, patch park, deactivate; reject inactive park; reject duplicate username.

- [ ] **Step 2: Implement router**

- [ ] **Step 3: `pytest tests/test_admin_mechanics.py -v` PASS**

- [ ] **Step 4: Commit** `feat(api): admin mechanics CRUD with single park binding`

---

### Task 5: Mechanic deps + auth routing helper

**Files:**
- Modify: `apps/api/src/robopark_api/deps.py`
- Modify: `apps/api/tests/conftest.py` (fixtures: `seed_mechanic`, `seed_park_with_tracker`)

**Add:**

```python
def require_approved_mechanic(user: User = Depends(require_user)) -> User:
    if (
        user.role != UserRole.mechanic.value
        or user.access_status != AccessStatus.approved.value
    ):
        raise HTTPException(status_code=403)
    return user


def get_mechanic_park(db: Session, user: User) -> Park | None:
    parks = db.scalars(
        select(Park).join(UserPark).where(UserPark.user_id == user.id)
    ).all()
    if len(parks) != 1:
        return None
    return parks[0]
```

Optional dependency `require_mechanic_with_park` → 403 if no exactly-one park (use on tool routes, not hub).

- [ ] **Step 1: Unit-style test via small endpoint or direct dep test in conftest consumer**

- [ ] **Step 2: Implement deps + fixtures**

- [ ] **Step 3: Commit** `feat(api): mechanic auth deps and park helper`

---

### Task 6: Emergency static data + VIN normalize + section renderer

**Files:**
- Create: `apps/api/data/emergency_sections.json` (copy structure from old bot `robopark_bot/data/emergency_sections.json`)
- Create: `apps/api/src/robopark_api/services/emergency_vin.py`
- Create: `apps/api/src/robopark_api/services/emergency_sections.py`
- Create: `apps/api/tests/test_emergency_sections.py`

**Port logic from old bot** (rewrite, do not import):
- `normalize_robot_id("447")` → `YASADR00000000447`
- `list_sections()` → `[{id, title}, ...]` in JSON order
- `render_section(payload: dict, section_id: str)` → `{ id, title, fields: [{ label, lines: string[] }] }`
- Include `emergency_error_rules.json` + `classify_robot_errors` only if `errors` section uses `formatter: errors_classify`

- [ ] **Step 1: Tests with fixture JSON payload (minimal robot dict)**

- [ ] **Step 2: Implement modules**

- [ ] **Step 3: `pytest tests/test_emergency_sections.py -v` PASS**

- [ ] **Step 4: Commit** `feat(api): emergency section maps and VIN normalization`

---

### Task 7: Tracker client + status filters + mechanic tasks API

**Files:**
- Modify: `apps/api/pyproject.toml` — add `httpx>=0.28.0` to main `dependencies`
- Create: `apps/api/src/robopark_api/services/tracker_filters.py`
- Create: `apps/api/src/robopark_api/services/tracker_client.py`
- Create: `apps/api/src/robopark_api/routers/mechanic_tasks.py`
- Create: `apps/api/tests/fixtures/tracker_issues.json`
- Create: `apps/api/tests/test_mechanic_tasks.py`

**Status filter enum (query param `status`):**

`all` | `moving` | `queued` | `waiting_team` | `waiting_parts` | `other`

Port `issue_status_bucket`, `filter_issues_by_status`, `sort_issues_oldest_first`, `count_status_buckets` from old `inline_park.py` (simplified, no pagination in Phase 3 web — return full filtered list capped at 200).

**Tracker client (sync httpx, timeout 30s):**

```python
def fetch_park_blockers(*, token: str, queue: str, park_tag: str) -> list[dict[str, Any]]:
    """Open blocker issues for queue filtered by park tag in summary/components."""

def build_issue_url(key: str) -> str:
    return f"https://tracker.yandex.ru/{key}"
```

Use Yandex Tracker REST API (`https://api.tracker.yandex.net/v2/issues/_search` or equivalent) with `Authorization: OAuth {token}`. Tests **must not** call live API — patch `httpx.Client.post` to return `fixtures/tracker_issues.json`.

**Route:** `GET /mechanic/tasks?status=all`

**Response:**

```python
class BlockerOut(BaseModel):
    key: str
    summary: str
    status: str
    robot: str | None
    created_at: str | None
    hours_created: str | None
    url: str
    bucket: str

class MechanicTasksOut(BaseModel):
    park_tag: str
    status: str
    counts: dict[str, int]
    items: list[BlockerOut]
```

**Errors:**
- 403 — not mechanic or no park
- 503 — missing Tracker token (`detail="tracker_token_not_configured"`)
- 409/400 style — park missing queue or `feature_blockers=false` (`detail="tasks_disabled_for_park"`)
- 502 — upstream failure

- [ ] **Step 1: Tests with mocked httpx** — mechanic sees only own park items; filter `moving`; 503 without token; disabled when `feature_blockers=false`.

- [ ] **Step 2: Implement filters, client, router**

- [ ] **Step 3: `pytest tests/test_mechanic_tasks.py -v` PASS**

- [ ] **Step 4: Commit** `feat(api): mechanic own-park tasks via Tracker`

---

### Task 8: Mechanic robot search API

**Files:**
- Extend: `apps/api/src/robopark_api/services/tracker_client.py`
- Create: `apps/api/src/robopark_api/routers/mechanic_robots.py`
- Create: `apps/api/tests/test_mechanic_robots.py`

**Route:** `GET /mechanic/robots/{query}/tickets`

- `query` = robot number (`447`, `a1517`) or ticket key (`ROBOPARK-123`)
- If looks like issue key → fetch by key; else search summaries across default queue(s) **without park tag filter** (port `_robot_search_variants` + `_summary_matches_robot` behavior)
- Returns `{ query, items: BlockerOut[] }` (reuse schema; `bucket` optional)

- [ ] **Step 1: Tests** — mock search returns multiple issues; key lookup; 503 without token

- [ ] **Step 2: Implement**

- [ ] **Step 3: `pytest tests/test_mechanic_robots.py -v` PASS**

- [ ] **Step 4: Commit** `feat(api): mechanic cross-park robot ticket search`

---

### Task 9: Emergency client + mechanic emergency routes

**Files:**
- Create: `apps/api/src/robopark_api/services/emergency_client.py`
- Create: `apps/api/src/robopark_api/routers/mechanic_emergency.py`
- Create: `apps/api/tests/fixtures/emergency_robot.json`
- Create: `apps/api/tests/test_mechanic_emergency.py`

**Emergency client:**

```python
EMERGENCY_BASE = "https://emergency.sdc.yandex-team.ru/api/v1"

def fetch_robot_payload(*, cookie: str, vin: str) -> dict[str, Any]:
    # GET {BASE}/{vin}/ with Cookie header; detect HTML/passport → raise EmergencyAuthError
```

On `EmergencyAuthError`, admin settings service marks cookie invalid (store flag in `platform_settings` e.g. `emergency_cookie_valid=false` or derive on GET from last error timestamp).

**Routes:**

| Method | Path | Body / params | Response |
|--------|------|---------------|----------|
| POST | `/mechanic/emergency/resolve` | `{ robot_number: str }` | `{ vin, sections: [{ id, title }] }` |
| GET | `/mechanic/emergency/{vin}/sections/{section_id}` | — | `{ id, title, fields: [{ label, lines }] }` |

- 400 — invalid robot number (ValueError from normalize)
- 503 — cookie not configured
- 401 — cookie expired (`detail="emergency_cookie_invalid"`)
- 502 — other upstream errors

Cache full Emergency payload in-memory per request/session is **not** required; for section GET, refetch or accept optional `POST` with cached payload later — **Phase 3:** refetch VIN on section GET (simplest, test with two mocked calls).

- [ ] **Step 1: Tests** — resolve robot → sections list; section render; 401 mock sets invalid flag; 503 without cookie

- [ ] **Step 2: Implement client + routes + wire invalid-cookie flag to admin GET**

- [ ] **Step 3: `pytest tests/test_mechanic_emergency.py -v` PASS**

- [ ] **Step 4: Commit** `feat(api): mechanic Emergency resolve and section views`

---

### Task 10: Web — admin extensions

**Files:**
- Modify: `apps/web/src/api.ts`
- Modify: `apps/web/src/pages/Admin.tsx`

**Admin UI sections (same page, headings):**

1. **Integration settings** — show masked tokens + updated_at; forms to PUT tracker token and emergency cookie; show cookie validity badge if API exposes it.
2. **Mechanics** — list; create form (username, password, park select from `GET /parks`); deactivate toggle; change park dropdown.
3. **Parks** (extend existing) — fields: `tracker_queue`, `group_id`, `chat_id`, feature toggles.

- [ ] **Step 1: Extend `api.ts` with admin methods**

- [ ] **Step 2: Wire Admin.tsx forms + lists**

- [ ] **Step 3: `npm run build` PASS**

- [ ] **Step 4: Commit** `feat(web): admin mechanics, integration settings, extended parks`

---

### Task 11: Web — mechanic hub and tool pages

**Files:**
- Modify: `apps/web/src/routes.ts`
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/api.ts`
- Modify: `apps/web/src/pages/Mechanic.tsx`
- Create: `apps/web/src/pages/MechanicTasks.tsx`
- Create: `apps/web/src/pages/MechanicRobotSearch.tsx`
- Create: `apps/web/src/pages/MechanicEmergency.tsx`
- Create: `apps/web/src/pages/MechanicNoPark.tsx`

**Routing (`pathForUser`):**

```typescript
case 'mechanic':
  if (user.parks?.length !== 1) return '/mechanic/no-park'
  return '/mechanic'
```

**Routes:**

| Path | Page |
|------|------|
| `/mechanic` | Hub — links to tasks, robot search, emergency |
| `/mechanic/tasks` | Filter tabs (6 buckets + counts) + issue list (read-only) |
| `/mechanic/robot-search` | Input + search button + results list |
| `/mechanic/emergency` | Robot number → section picker → section detail |
| `/mechanic/no-park` | Static message to contact admin |

Show API error messages for 503/401 (misconfigured integrations) in plain language.

- [ ] **Step 1: Routes + api methods**

- [ ] **Step 2: Pages (minimal styling, match Operator/Admin patterns)**

- [ ] **Step 3: `npm run build` PASS**

- [ ] **Step 4: Commit** `feat(web): mechanic hub and three tool pages`

---

### Task 12: Docs + env + full verification

**Files:**
- Modify: `README.md` — Phase 3 flows: admin creates mechanic, sets tokens, mechanic tools
- Modify: `.env.example`, `deploy/host.env.example` — note secrets are DB-managed (no new env keys required except optional comment)
- Cross-link Phase 3 spec from README

- [ ] **Step 1: Update docs**

- [ ] **Step 2: Full gates**

Run:
```bash
cd apps/api && pytest -v
cd apps/web && npm run build
```

Expected: all tests green (baseline 65 + new Phase 3 tests); web build OK.

- [ ] **Step 3: Manual checklist** (implementer report):
  1. Admin sets Tracker token + Emergency cookie in UI
  2. Admin creates park with `tracker_queue` + mechanic on that park
  3. Mechanic login → hub → tasks list with filters
  4. Robot search returns tickets for a robot number
  5. Emergency: robot number → pick section → see fields
  6. Mechanic without park → `/mechanic/no-park`
  7. Tasks disabled when `feature_blockers=false`

- [ ] **Step 4: Commit** `docs: document Phase 3 mechanic flows`

---

## Spec coverage

| Spec item | Task |
|-----------|------|
| Extended `parks` fields | 1, 3 |
| `platform_settings` + masked admin GET | 2 |
| Admin mechanics CRUD (1 park) | 4 |
| `require_approved_mechanic` | 5 |
| `emergency_sections.json` + renderer | 6 |
| Mechanic tasks (own park, filters) | 7 |
| Robot search (cross-park) | 8 |
| Emergency resolve + sections | 9 |
| Admin UI (mechanics, settings, parks) | 10 |
| Mechanic web routes + three tools | 11 |
| README / verification | 12 |
| Non-goals (Telegram, keep-alive, deep tickets) | Global Constraints |

## Deferred

- Emergency keep-alive background job
- Operator/admin Tracker and Emergency tools
- Ticket deep-work (status edits, comments)
- Postgres cutover
- Live VPS deploy

## Execution note

Prefer TDD on API tasks 1–9. Mock all upstream HTTP in tests. Use feature worktree; `gitarius scan` before commit if available. Do not push unless asked. After implementation, user typically requests PR via `origin pr create`.
