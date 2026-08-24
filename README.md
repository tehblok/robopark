# Robopark

Web-first fleet operations system (admin / operator / mechanic).

- **Primary:** website on local host (API + app); remote access via **WireGuard on a VPS** (no app on the VPS, no public URL required).
- **Reserve:** Telegram bots with feature parity, as thin clients to the same API (not in Phase 1).
- **Clean slate:** new codebase; prior bot repo is reference only, not a dependency.

## Phase 1

Platform skeleton: FastAPI + React monorepo, session auth, role cabinets, SQLite on host, Docker Compose on the host; VPS optional for WireGuard only.

Design: [`docs/superpowers/specs/2026-08-21-robopark-platform-phase1-design.md`](docs/superpowers/specs/2026-08-21-robopark-platform-phase1-design.md)

## Phase 2

Operator onboarding and parks: shared-password registration, admin/royal access
approval with park assignment, minimal park CRUD, and operator park-request
inbox.

Design: [`docs/superpowers/specs/2026-08-22-robopark-phase2-operator-onboarding-design.md`](docs/superpowers/specs/2026-08-22-robopark-phase2-operator-onboarding-design.md)

Set `OPERATOR_SHARED_PASSWORD` in `.env` (local) or `host.env` (deploy). When
unset or empty, `POST /auth/register` returns 403 and the register page is
closed. Never commit a real value.

### Registration and access

1. Operator opens `/register`, enters the shared password plus username and
   password, then signs in at `/login`.
2. New operators start as `access_status=pending` and land on `/operator/pending`.
3. Admin or royal creates parks in `/admin`, then approves the access request
   with at least one active park — the operator moves to `/operator`.
4. Reject sets `access_status=rejected` and routes to `/operator/rejected`; there
   is no self-serve re-apply.

### Parks and park requests

- Admin/royal manage parks (name, unique tag, active flag) from `/admin`.
- Approved operators see assigned parks and may request additional parks; admin
  approves or rejects those requests in the same inbox.

## Phase 3

Mechanic flows: admin-created mechanic accounts (exactly one park, immediately
approved), integration settings for Tracker token and Emergency cookie, extended
park Tracker fields, and mechanic tools for own-park tasks, cross-park robot
search, and Emergency VIN checks.

Design: [`docs/superpowers/specs/2026-08-22-robopark-phase3-mechanic-flows-design.md`](docs/superpowers/specs/2026-08-22-robopark-phase3-mechanic-flows-design.md)

Tracker token and Emergency cookie are stored in the database and configured from
`/admin` — they are not environment variables. After creating a park with
`tracker_queue` and a mechanic assigned to it, the mechanic cabinet exposes
`/mechanic/tasks`, `/mechanic/robot-search`, and `/mechanic/emergency`.

## Phase 4

Operator tools: approved operators use the hub at `/operator` for read-only
Tracker workflows — blockers by assigned park, cross-park robot search, and the
«Сейчас по Tracker» live metrics snapshot. Park assignment and park requests
remain at `/operator/parks`.

Design: [`docs/superpowers/specs/2026-08-22-robopark-phase4-operator-tools-design.md`](docs/superpowers/specs/2026-08-22-robopark-phase4-operator-tools-design.md)

These tools require a platform Tracker OAuth token in `/admin` and, per park,
`tracker_queue` plus feature flags: `feature_blockers` for the blockers list and
`feature_reports` for the now-report. Parks missing queue or flags are skipped
in the report with an inline reason.

## Phase 5

Tracker Core + Actions: unified `/tracker/*` API with role-aware ACL for
`admin`/`operator`/`mechanic`, issue read endpoints, and write actions
(comment/assign/unassign/transition/close). Queue scope is enforced on the
backend for every issue key request to prevent cross-queue access.

New endpoints:

- `GET /tracker/issues`
- `GET /tracker/issues/{key}`
- `GET /tracker/issues/{key}/comments`
- `GET /tracker/transitions/{key}`
- `POST /tracker/issues/{key}/comment`
- `POST /tracker/issues/{key}/assign`
- `POST /tracker/issues/{key}/unassign`
- `POST /tracker/issues/{key}/transition`
- `POST /tracker/issues/{key}/close`

Admin policy toggles are available at:

- `GET /admin/settings/tracker-policy`
- `PUT /admin/settings/tracker-policy`

## Phase 6

Emergency is now a shared, role-aware workflow for mechanics, operators,
admins, and royal users. Mechanics see the operational sections, operators
additionally see `position_route` and `metadata`, and only admins/royal users
see `service_raw`. The legacy `/mechanic/emergency/*` routes remain available
for compatibility.

Emergency section, field, order, enabled-state, and role configuration lives in
the database. Migration `0004_phase6_emergency_config` creates the tables and
seeds them from `apps/api/data/emergency_sections.json`; after migration, use
the admin UI at `/admin/emergency/config` to create, edit, reorder, enable, or
export sections instead of editing the seed file.

Robot payloads are cached in-process for 5 seconds, with one in-flight upstream
request per VIN. The API also runs a keep-alive loop over the 20 most recently
used VINs (or the optional seed VIN when the ring is empty), records cookie
validity, and marks it invalid after an upstream 401. Emergency pages load on
user actions and do not poll from browser timers.

## UI shell и дашборд

После входа все роли попадают в общий **AppShell**: слева сайдбар «Робопарк
Сервис», сверху — выбор **Парка**, имя пользователя и меню.

**Тема:** в меню пользователя (правый верхний угол) переключатель «Светлая /
Тёмная тема». Выбор сохраняется в `localStorage` (`robopark-theme`); по
умолчанию — светлая.

**Парк:** дашборд и связанные экраны зависят от выбранного парка в шапке.
У механика парк зафиксирован; у оператора и админа — выпадающий список
назначенных парков.

**Дашборд** (`/dashboard`): KPI и график «пришли / ушли» блокеров за 7 дней.
Сводка берётся из Tracker (now-report); история графика — из локальной БД.
Фоновый job API сканирует Tracker **каждые 2 часа** и пишет бакеты per-park;
пока job не отработал, график может быть пустым. Нужны `tracker_queue`, `tag`
и OAuth-токен Tracker в `/admin`.

**Заглушки «Скоро»:** Карта, Обучение, Помощь — пункты меню видны, контент
появится позже.

Design: [`docs/superpowers/specs/2026-08-24-robopark-ui-shell-dashboard-design.md`](docs/superpowers/specs/2026-08-24-robopark-ui-shell-dashboard-design.md)

## Local development

### API

Python 3.12 or newer is required. Create the environment, install the API, copy
the example configuration, run the database migration, and start Uvicorn:

```bash
cd apps/api
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp ../../.env.example .env
mkdir -p data
alembic upgrade head
uvicorn robopark_api.main:app --reload --app-dir src
```

Before the first start, set `SEED_USERNAME`, `SEED_PASSWORD`, and `SEED_ROLE`
in `apps/api/.env`. The seed user is created only when it does not already
exist; use a strong password and remove `SEED_PASSWORD` from the environment
after the user has been created. To enable operator self-registration, also set
`OPERATOR_SHARED_PASSWORD` in `apps/api/.env`. The API is available at
`http://127.0.0.1:8000`; check it with
`curl http://127.0.0.1:8000/health`.

### Web

With the API running, start the web app in another terminal:

```bash
cd apps/web
npm install
npm run dev
```

Vite serves the app on `http://localhost:5173` and proxies `/api/*` to the
local API, stripping the `/api` prefix.

## Host installation

The host runs the API, SQLite database, and web app:

```bash
git clone <repository-url> robopark
cd robopark/deploy
cp host.env.example host.env
```

Edit `host.env`: strong `SEED_PASSWORD`, optional `OPERATOR_SHARED_PASSWORD`, and
`CORS_ORIGINS=http://10.8.0.2:8080` (host VPN address — see deploy docs).

```bash
export HOST_ENV_FILE=./host.env
docker compose up -d --build
```

`host.env` is gitignored. For **VPN access**, keep `COOKIE_SECURE=false` (HTTP
inside WireGuard). The API is not exposed on port 8000 — use the web container
on **port 8080** (SPA + `/api` proxy).

Full topology: [`deploy/README.md`](deploy/README.md).

## VPS (WireGuard only)

The VPS does **not** run Robopark. It runs the WireGuard hub so operators and
the host can reach each other without a public IP on the host.

```bash
git clone <repository-url> robopark
cd robopark/deploy/vps
cp wg.env.example wg.env
# set SERVERURL to the VPS public IP; keep host as the first PEER name
docker compose --env-file wg.env up -d
```

Copy `config/peer_host/` to the Armbian host, enable `wg0`, then operators import
their peer configs and open **http://10.8.0.2:8080**.

Details: [`deploy/vps/README.md`](deploy/vps/README.md), [`deploy/wireguard/host-peer.md`](deploy/wireguard/host-peer.md).

## Phase 1 boundaries

This repository is a clean implementation and has no runtime or build
dependency on the old bot repository.

Phase 1 does not include:

- Telegram bots or feature parity clients.
- Tracker, Emergency, reports, blockers, SLA, or onboarding flows.
- Automated WireGuard key distribution to operators (configs are manual from VPS `config/`).
- PostgreSQL migration or production hardening beyond the platform skeleton.
