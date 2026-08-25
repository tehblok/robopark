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
   with at least one active park — the operator moves to `/dashboard`.
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
`tracker_queue` and a mechanic assigned to it, the shared shell exposes
`/tasks`, `/robots/search`, and `/emergency` (legacy `/mechanic/*` URLs redirect).

## Phase 4

Operator tools: approved operators use the hub at `/dashboard` for read-only
Tracker workflows — blockers by assigned park (`/tasks`), cross-park robot search
(`/robots/search`), and the «Сейчас по Tracker» live metrics snapshot
(`/analytics`). Park assignment and park requests remain at `/operator/parks`.

Design: [`docs/superpowers/specs/2026-08-22-robopark-phase4-operator-tools-design.md`](docs/superpowers/specs/2026-08-22-robopark-phase4-operator-tools-design.md)

These tools require a platform Tracker OAuth token in `/admin` and, per park,
`tracker_queue` plus feature flags: `feature_blockers` for the blockers list and
`feature_reports` for the now-report. Parks missing queue or flags are skipped
in the report with an inline reason.

## Phase 5

Tracker Core + Actions: unified `/tracker/*` API with role-aware ACL for
`admin`/`operator`/`mechanic`, issue read endpoints, and write actions
(comment/assign/unassign/transition/close).

**Scope is fail-closed.** For every issue an operator or mechanic touches, the
backend requires proof that the issue belongs to one of their parks:

1. the issue queue must be present and among the user's park queues;
2. the issue must carry the tag of one of the user's parks.

An issue tagged with a *different* park is always denied. An issue with no park
tag is reachable only while the `operator_show_untagged` policy is enabled, and
never for mechanics. Anything unverifiable — missing queue, no park assigned —
is denied rather than allowed. List endpoints silently filter out-of-scope
issues; single-issue endpoints return `403 tracker_issue_out_of_scope`.

`GET /tracker/issues` is paginated (`limit`, `offset`, default 50, max 200) and
returns `total` / `has_more` instead of silently truncating the result.

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

## Репорты

Цепочка «механик → оператор → админ» в `/reports`. KPI Tracker (now-report)
остаётся на **Дашборде**, не смешивается с человеческими репортами.

Design: [`docs/superpowers/specs/2026-08-24-robopark-reports-workflow-design.md`](docs/superpowers/specs/2026-08-24-robopark-reports-workflow-design.md)

### Виды (`kind`)

| kind | Кто создаёт | Получатель |
|------|-------------|------------|
| `ticket_question` | Механик (форма «Вопрос по тикету») | Оператор парка |
| `ticket_close_review` | Автоматически при «Закрыть» тикет в UI | Оператор парка |
| `mechanic_problem` | Механик (форма «Проблема») | Оператор парка |
| `escalation_to_admin` | Оператор (эскалация) | Админ / royal |

Для `ticket_question` и `ticket_close_review` обязателен ключ тикета Tracker;
для `mechanic_problem` — опционален.

### Закрытие тикета → оператор

Механик нажимает «Закрыть» в задачах Tracker. Бэкенд сначала выполняет
переход в Tracker; только после успешного закрытия создаётся репорт
`ticket_close_review` со статусом `open` для операторов этого парка. Повторное
открытое ревью по тому же `(park_id, tracker_key)` не дублируется.

### Действия получателя

- **Вернуть** — статус `returned`, обязателен комментарий; механик видит его в
  `/reports` и в списке задач (возвращённые close-review).
- **Готово** — статус `done`, репорт уходит из активного inbox (не удаляется).
- **Закрыть** — только закрыть карточку в UI, статус не меняется.
- **Эскалировать** (оператор) — новый репорт `escalation_to_admin` в inbox
  админа.

### Бейдж в меню

На пункте «Репорты» в сайдбаре:

- **Механик** — число своих репортов со статусом `returned`.
- **Оператор** — число `open` в inbox по назначенным паркам.
- **Админ / royal** — число `open` эскалаций.

Счётчик обновляется при навигации и при возврате на `/reports`.

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

**Local demo accounts:** set `DEV_SEED=true` in `apps/api/.env` and restart the
API. Logins and passwords — [`docs/DEV-ACCOUNTS.md`](docs/DEV-ACCOUNTS.md)
(for example `royal` / `RoboparkRoyal!1`, `operator` / `RoboparkOperator!1`).

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

## Security

### Secrets at rest

The Tracker OAuth token and the Emergency cookie are stored in the database and
encrypted with a key derived from `SECRET_KEY` (Fernet, AES-128-CBC + HMAC).
Without `SECRET_KEY` the API still runs but keeps secrets as plaintext and logs
a warning — set it in `.env` / `host.env`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Values written before encryption keep working and are upgraded to ciphertext on
the next save. Changing or losing the key makes stored secrets unreadable: the
API reports the integration as «not configured» and the value must be re-entered
in `/admin`.

### Passwords and brute force

Registration and admin-created accounts must satisfy a password policy
(`PASSWORD_MIN_LENGTH`, default 12, plus three of four character classes).
`POST /auth/login` and `POST /auth/register` are rate limited per
username + address; after `LOGIN_MAX_ATTEMPTS` failures the pair is locked for
`LOGIN_LOCKOUT_SECONDS` and the API answers `429` with `Retry-After`.

Changing a mechanic's password or deactivating the account revokes their active
sessions immediately. Expired sessions are purged on login and by an hourly
background job.

### Audit trail

All Tracker writes go through one service token, so Tracker itself cannot tell
users apart. Every action is therefore recorded in `audit_log` — actor, role,
target issue, outcome (`success` / `failure` / `denied`), and address — and
comments written through the platform are signed with the real author.

- `GET /admin/audit` — filter by `action`, `actor_user_id`, `target_id`,
  `park_id`; paginated.
- `GET /admin/audit/actions` — known action names for UI filters.

Denied attempts are recorded too, so a blocked cross-park action leaves a trace.

## Health and operations

- `GET /health` — liveness, dependency-free.
- `GET /health/ready` — readiness: verifies the database and reports integration
  state; returns `503` when the database is unreachable.

Both Compose services declare healthchecks, the API container runs as an
unprivileged user, and SQLite is opened in WAL mode with `busy_timeout` and
enforced foreign keys so the background jobs do not collide with requests.

## Continuous integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push and
pull request: ruff lint, the pytest suite (including model/migration parity),
web lint + type check + build + vitest, and a build of both Docker images.

Locally:

```bash
cd apps/api && .venv/bin/ruff check . && .venv/bin/python -m pytest -q
cd apps/web && npm run build && npm test && npm run lint && npm run check-nav
```

Remaining UI/UX backlog: [`docs/UI-REFACTOR-SPEC.md`](docs/UI-REFACTOR-SPEC.md).

## Scope boundaries

This repository is a clean implementation and has no runtime or build
dependency on the old bot repository.

Not included:

- Telegram bots or feature parity clients.
- Automated WireGuard key distribution to operators (configs are manual from VPS `config/`).
- PostgreSQL migration; SQLite on the host remains the storage engine.
- Per-user Tracker credentials — the platform uses one service token and
  attributes actions through `audit_log` and comment signatures.
