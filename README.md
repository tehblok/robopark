# Robopark

Web-first fleet operations system (admin / operator / mechanic).

- **Primary:** website on local host (API + app), public HTTPS via VPS tunnel (no white IP on the host).
- **Reserve:** Telegram bots with feature parity, as thin clients to the same API (not in Phase 1).
- **Clean slate:** new codebase; prior bot repo is reference only, not a dependency.

## Phase 1

Platform skeleton: FastAPI + React monorepo, session auth, empty role cabinets, SQLite on host, one Docker Compose with `ROBOPARK_ROLE=host|vps`.

Design: [`docs/superpowers/specs/2026-08-21-robopark-platform-phase1-design.md`](docs/superpowers/specs/2026-08-21-robopark-platform-phase1-design.md)

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
after the user has been created. The API is available at
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
cp host.env.example .env
```

Edit `.env`, set a strong `SEED_PASSWORD`, keep `ROBOPARK_ROLE=host`, and then
start the host profile:

```bash
export ROBOPARK_ROLE=host
export HOST_ENV_FILE=./.env
docker compose --profile host up -d --build
```

The web app is served on `http://<host>:8080`. The API container runs
`alembic upgrade head` before Uvicorn starts, and stores SQLite data in the
`robopark_data` volume.

## VPS installation

The VPS runs Caddy only; **the API and application database do not run on the
VPS**.

```bash
git clone <repository-url> robopark
cd robopark/deploy
cp vps.env.example .env
```

Set `ROBOPARK_ROLE=vps`, `PUBLIC_HOST`, and `TUNNEL_UPSTREAM` in `.env`. Replace
the placeholders in `Caddyfile.vps.example` and choose/configure a transport
from `tunnel.env.example`; tunnel credentials must stay in untracked files.
The tunnel itself is operator-managed and is not started by this Compose file.

```bash
export ROBOPARK_ROLE=vps
export VPS_ENV_FILE=./.env
docker compose --profile vps up -d
```

Never enable both Compose profiles on one machine.

## Phase 1 boundaries

This repository is a clean implementation and has no runtime or build
dependency on the old bot repository.

Phase 1 does not include:

- Telegram bots or feature parity clients.
- Tracker, Emergency, reports, blockers, SLA, or onboarding flows.
- A live tunnel/VPS deployment; that waits for user-provided SSH access.
- PostgreSQL migration or production hardening beyond the platform skeleton.
