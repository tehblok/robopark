# Robopark Phase 6 — Emergency DB config + multi-role access

**Date:** 2026-08-24  
**Status:** approved for planning  
**Depends on:** Phase 3 mechanic Emergency, Phase 4/5 platform (sessions, roles, admin settings)

## Goal

Перевести Emergency с статичного `emergency_sections.json` на DB-конфиг с админ-CRUD, открыть просмотр данных для `mechanic` / `operator` / `admin` / `royal` с разными наборами секций, добавить общий короткий кэш payload и фоновый cookie keep-alive — **без** карты, анимаций и live-телеметрии UI.

## Non-goals

- Map / robot glyph / speedometer / streaming telemetry UI.
- Telegram bots / notify parity (API должен быть совместим позже).
- Replacing admin cookie paste UX (remain in integrations settings).
- Park-scoped Emergency VIN filtering.

## Decisions

| Topic | Choice |
|-------|--------|
| Config storage | Normalized SQL tables (sections, fields, section↔role) |
| Seed | One-time migrate from `apps/api/data/emergency_sections.json` |
| Viewer roles | mechanic + operator + admin + royal |
| Role section sets | mechanic ⊂ operator ⊂ admin/royal (seed defaults; editable) |
| Admin editor | Full CRUD UI under `/admin/emergency/config` |
| Payload cache | Shared in-process by VIN, **TTL 5s**, single-flight |
| Cookie keep-alive | Background job on ring of last K=20 VINs; interval 90–120s |
| Empty ring | Optional `emergency_keepalive_seed_vin` setting; else no idle upstream calls |
| Browser polling | None — manual refresh only |
| Legacy routes | Keep `/mechanic/emergency/*` as aliases for one release, then drop |

## Architecture

```
UI (mechanic | operator | admin)
  → /emergency/*  (auth + role section filter)
  → EmergencyCache (VIN → payload, TTL 5s, single-flight)
  → EmergencyClient → https://emergency.sdc.yandex-team.ru/api/v1/{vin}/
  → SectionRenderer (DB map + formatters)

Admin CRUD → /admin/emergency/sections|fields
Cookie CRUD → existing /admin/settings/*
KeepAliveJob → ring of last K VINs (cookie liveness only)
```

### Components

| Piece | Responsibility |
|-------|----------------|
| `emergency_sections` tables + Alembic migration | Persist section/field/role maps |
| `emergency_config` service | Load/save config; seed; role filter |
| `emergency_cache` | TTL 5s shared cache + single-flight |
| `emergency_client` (existing) | Upstream GET; auth/error detection |
| `emergency_sections` renderer (evolve) | Dig paths / formatters from DB config |
| `routers/emergency.py` | Shared resolve + section endpoints |
| `routers/admin_emergency.py` | Admin CRUD + reorder + export |
| `keepalive` job | Periodic ring poll; mark cookie invalid on 401 |
| Web viewer pages | Role cabinets → shared Emergency viewer |
| Web admin config page | Full section/field/role editor |

## Data model

### `emergency_sections`

| Column | Type | Notes |
|--------|------|-------|
| `id` | string PK | Stable section key (`status`, `batteries`, …) |
| `title` | string | Display title |
| `sort_order` | int | UI order |
| `is_enabled` | bool | Soft disable without delete |
| `formatter` | string nullable | e.g. `errors_classify` |
| `meta_json` | text nullable | Extra keys (`covered_top_level`, …) |

### `emergency_fields`

| Column | Type | Notes |
|--------|------|-------|
| `id` | int PK | Surrogate |
| `section_id` | FK | Cascade |
| `path` | string | Dot path into payload |
| `label` | string | Display label |
| `sort_order` | int | Field order |

### `emergency_section_roles`

| Column | Type | Notes |
|--------|------|-------|
| `section_id` | FK | |
| `role` | string | `mechanic` / `operator` / `admin` / `royal` |
| PK | `(section_id, role)` | |

### Platform settings additions

- `emergency_keepalive_seed_vin` (optional string) — used when ring is empty.
- Existing: `emergency_cookie`, `emergency_cookie_valid`.

### Seed defaults

- Import all sections/fields from current `emergency_sections.json`.
- Default role grants (editable after seed):
  - **mechanic:** diagnostic core (status, batteries, errors, wheels, parktronics, localization, control, hardware_hud, logs_disk) — exclude `service_raw` unless granted.
  - **operator:** mechanic set + `position_route` + `metadata`.
  - **admin/royal:** all enabled sections including `service_raw`.

Exact seed lists are fixed in migration/seed code to match product defaults above.

## API contract

### Viewer (approved mechanic / operator / admin / royal)

- `POST /emergency/resolve` `{ "robot_number": "..." }`  
  → `{ vin, sections: [{ id, title }] }` filtered by role + `is_enabled`.
- `GET /emergency/{vin}/sections/{section_id}`  
  → `{ id, title, fields: [{ label, lines }] }`  
  → `404` if unknown/disabled/not allowed for role.

Legacy aliases (temporary):

- `POST /mechanic/emergency/resolve`
- `GET /mechanic/emergency/{vin}/sections/{section_id}`

### Admin config (admin / royal)

- `GET /admin/emergency/sections` — full tree with fields + roles
- `POST /admin/emergency/sections` — create
- `PATCH /admin/emergency/sections/{id}` — update title/enabled/formatter/meta/roles
- `DELETE /admin/emergency/sections/{id}`
- `PUT /admin/emergency/sections/reorder` — ordered ids
- `POST /admin/emergency/sections/{id}/fields` — create field
- `PATCH /admin/emergency/fields/{id}` — update
- `DELETE /admin/emergency/fields/{id}`
- `GET /admin/emergency/export` — JSON compatible with seed shape
- Optional: `POST /admin/emergency/import` — replace/merge from JSON (admin-only; validated)

Cookie endpoints unchanged.

### Errors

| Code | Detail |
|------|--------|
| 503 | `emergency_cookie_not_configured` |
| 401 | `emergency_cookie_invalid` |
| 502 | upstream / parse failure |
| 400 | bad robot id / validation |
| 403 | role / ACL |
| 404 | section not found or not allowed |

## Cache

- Key: normalized VIN.
- Value: raw upstream JSON (+ fetched_at).
- TTL: **5 seconds**.
- Concurrent misses for same VIN share one in-flight request (single-flight).
- On 401: invalidate VIN entry; set `emergency_cookie_valid=false`.
- Successful user fetch updates keep-alive ring (move VIN to end, truncate to K=20).

## Keep-alive

| Param | Default |
|-------|---------|
| Ring size K | 20 |
| Cycle interval | 90–120 s |
| Gap between VINs | ≥ 0.9 s |
| Empty ring | use `emergency_keepalive_seed_vin` if set; otherwise skip cycle |
| Runner | in-process asyncio/background task started with API lifespan |

Behavior:

1. Read cookie; if missing → skip.
2. For each VIN in ring (or seed): GET upstream.
3. Success → touch `last_ok_at` meta (platform_settings or small keepalive meta key).
4. 401 / passport HTML → mark cookie invalid; stop cycle early.

Keep-alive must **not** drive browser UI and must not re-render sections.

## Web UX

### Viewer

- Routes: `/mechanic/emergency`, `/operator/emergency`, `/admin/emergency`.
- Shared component: robot input → resolve → section list → section detail.
- Manual «Обновить» only (no interval polling).
- Russian copy via existing i18n patterns.

### Admin config

- `/admin/emergency/config` (+ link from Admin integrations/hub).
- CRUD for sections, fields, role checkboxes, enable toggle, reorder.
- Export JSON; optional import.
- Does not auto-fetch robots in background.

## Testing

- Migration creates tables and seeds from JSON.
- Role filter: mechanic cannot open admin-only section.
- Cache: two concurrent resolve/section for same VIN → one upstream call.
- Cache expiry after 5s allows second upstream call.
- Keep-alive 401 flips cookie validity.
- Admin CRUD ACL (operator → 403).
- Existing mechanic flow still works via alias or migrated client.

## Rollout

1. Migrate + seed DB; keep file as export reference.
2. Ship shared `/emergency/*` + cache + keep-alive.
3. Point mechanic/operator/admin UIs at new API.
4. Ship admin config UI.
5. Remove `/mechanic/emergency/*` aliases in a follow-up once unused.

## Risks

| Risk | Mitigation |
|------|------------|
| Keep-alive load when idle | Empty ring + optional seed only |
| Stale section map in memory | Load config from DB per request or short config TTL; invalidate on admin write |
| Formatter/meta loss on normalize | `meta_json` + formatter column |
| Alias confusion | Document deprecation; one-release dual paths |
