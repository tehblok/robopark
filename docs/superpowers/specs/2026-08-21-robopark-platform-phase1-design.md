# Design: Robopark platform Phase 1 (web skeleton)

**Date:** 2026-08-21  
**Status:** approved (conversation)  
**Repo:** `robopark` (clean slate; prior Telegram bot repo is reference only)

## Goal

Ship a **web-first platform skeleton**: FastAPI + React monorepo, role-based login, empty role cabinets, SQLite on the LAN host, and a single Docker Compose install that runs in **`host`** or **`vps`** mode. No Tracker/Emergency/bot clients in this phase.

## Product decisions (locked)

| Item | Choice |
|------|--------|
| Primary UI | Website |
| Telegram | Reserve client later; **full feature parity** with the site via the same API; **no bot code in Phase 1** |
| Topology | Brain on Armbian host (no white IP); VPS = HTTPS + reverse proxy + tunnel |
| Clean slate | New repo; do not carry old bot handlers, systemd four-bot units, or JSON-config legacy |
| Stack | FastAPI + React/Vite |
| Layout | Monorepo: `apps/api` + `apps/web` + `deploy/` |
| Auth (Phase 1) | Username/password + httpOnly session cookie; hashed passwords; seed users |
| Onboarding flows | Operator shared-password + admin approve, mechanic park password, park requests — **later phases** |
| Data store | SQLite on host now; SQLAlchemy (+ Alembic) so Postgres is a later swap |
| Install | One Docker Compose; required `ROBOPARK_ROLE=host\|vps` (or equivalent Compose profiles) |

## Non-goals (Phase 1)

- Yandex Tracker / Emergency integrations
- Reports, blockers, SLA, requests inbox
- Operator access-request / park-request workflows
- Telegram bots
- Mandatory live deploy (configs and docs yes; applying to real VPS/host when access is provided)
- Migrating or vendoring code from `robopark_tehblokdan`

## Topology

```
Internet → VPS (Ubuntu): TLS, reverse proxy, tunnel endpoint
              ↓ outbound tunnel (host has no public IP)
         Armbian host: FastAPI + SQLite (+ later adapters to LAN APIs)
              ↑
         React SPA (served via proxy / static from host stack)
```

- **Only `host` role** runs API and database.
- **`vps` role** must not start API or mount the app DB — mistake-proofing for “one compose, two places”.
- Future Telegram bots (not Phase 1) call the same API on the host (reachable via tunnel or LAN as designed later).

## Repository layout

```
robopark/
  apps/api/       FastAPI app, SQLAlchemy models, Alembic, session auth
  apps/web/       React + Vite SPA
  deploy/         docker-compose, role env examples, Caddy/nginx + tunnel templates
  docs/           designs and plans
  README.md
  .env.example
```

## Auth and roles

Roles for seed and routing (names aligned with prior domain knowledge):

| Role | Phase 1 behavior |
|------|------------------|
| `royal` | Full admin cabinet access (seed; manage users later) |
| `admin` | Admin cabinet shell |
| `operator` | Operator cabinet shell |
| `mechanic` | Mechanic cabinet shell |

Flow:

1. `POST /auth/login` with username + password → set secure httpOnly session cookie.
2. `POST /auth/logout` → clear session.
3. `GET /auth/me` → current user + role (for SPA routing).
4. Unauthenticated API calls → 401; web redirects to login.

Passwords stored hashed (e.g. Argon2 or bcrypt). Seed accounts created on first migrate/bootstrap (credentials only via env / one-time bootstrap file — never commit secrets).

Park tags and rich RBAC from the old system are **out of scope** for Phase 1 beyond storing `role` on the user row.

## Web cabinets (empty)

After login, SPA routes by role:

- `/login`
- `/admin` — placeholder (“Admin”)
- `/operator` — placeholder (“Operator”)
- `/mechanic` — placeholder (“Mechanic”)

No cross-role menus. Wrong role hitting another cabinet → 403 / redirect home.

Pinned reply keyboards and Telegram UX patterns are irrelevant here.

## API surface (Phase 1)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness (no auth) |
| POST | `/auth/login` | Session login |
| POST | `/auth/logout` | End session |
| GET | `/auth/me` | Current user |

Optional later in same phase if cheap: `GET /ready` (DB ping).

CORS: allow the web origin(s) configured by env; cookies `SameSite` appropriate for reverse-proxy same-site or documented cross-site setup.

## Data

- SQLite file on **host** volume (path via env).
- ORM: SQLAlchemy 2.x; migrations: Alembic.
- Minimal tables: `users` (id, username, password_hash, role, is_active, created_at), `sessions` (or server-side session store equivalent).

No `config.json` parks/RBAC copy from the bot era.

## Docker install model

Single Compose project, **role required**:

| `ROBOPARK_ROLE` | Runs | Does not run |
|-----------------|------|--------------|
| `host` | `api`, `web` (or web build served by api/proxy), DB volume | public TLS terminator (optional local-only bind) |
| `vps` | reverse proxy + tunnel sidecar/docs | `api`, SQLite |

Implementation detail for the plan: Compose profiles (`--profile host` / `--profile vps`) and/or entry validation that exits if role/services mismatch.

Goal: one install story (“clone, set role, compose up”) without a mega-container that runs API on the VPS.

## Security / stability / latency (constraints)

- Host has no white IP → only **outbound** tunnel to VPS.
- Secrets in env / Docker secrets; `.env` gitignored.
- Prefer binding API to tunnel/internal interfaces on host, not exposing LAN APIs to the internet.
- Keep request path short: browser → VPS proxy → tunnel → host API.

## Success criteria

1. Fresh clone + `ROBOPARK_ROLE=host` brings up API + web; seed user can log in and see the correct empty cabinet.
2. `ROBOPARK_ROLE=vps` compose config exists and does not start API/DB services.
3. `/health` and `/auth/*` work; no dependency on the old bot package.
4. README documents local dev and the host/vps install split; deploy templates live under `deploy/`.
5. SQLite persists across container restarts on host; Alembic migration applies cleanly.

## Follow-on phases (not this spec)

1. Operator onboarding (shared password → admin approve → ≥1 park) and park-request UX.  
2. Mechanic flows (tasks / robot / Emergency) on web.  
3. Operator working tools (reports, blockers, requests stub → real inbox).  
4. Thin Telegram clients against the same API (parity).  
5. Optional Postgres cutover.

## Reference only

Prior bot designs under `robopark_tehblokdan` (roles, four-bots, mechanic UX, Emergency sections) inform domain language; they are **not** implementation dependencies for Phase 1.
