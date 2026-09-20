# Deployment: host + Tuna

Для чистого установочного архива **0.1.45** с PostgreSQL 17 на Armbian 26/Ubuntu 22 ARM64 используйте [руководство оператора](INSTALL-ARMBIAN-RU.md) и [приёмку 200 пользователей](CAPACITY-RU.md). Host-тесты проверяют install/update-over-install; реальная проверка systemd/Docker/Tuna на целевом устройстве остаётся отдельным deployment gate. Ниже сохранён ручной Compose-путь.

Для Armbian 26 с 8 ГБ, 200 пользователей и переноса на Ubuntu с 32 ГБ:
[пошаговая инструкция общего сервера](SHARED-HOST.md).

Robopark runs **only on the host** (Armbian / home server): API, PostgreSQL 17, web UI.

Remote access uses **[Tuna](https://tuna.am/docs/)** — an HTTP reverse tunnel (ngrok-class).
There is **no VPS** and **no WireGuard**. Mechanics open an HTTPS link in the browser.

```
  Mechanic laptop/phone                         Host (no white IP)
  ┌─────────────────┐                          ┌──────────────────────┐
  │ Browser         │──HTTPS──► tuna.am ──────►│ tuna agent           │
  │ https://….tuna… │                          │   ↓ localhost:8080   │
  └─────────────────┘                          │ Docker: web + api    │
                                               └──────────────────────┘
```

Docs: [Tuna overview](https://tuna.am/docs/), [HTTP tunnels](https://tuna.am/docs/tunnels/http/), [Install](https://tuna.am/docs/guides/install/).

## 1. Host (application)

```sh
git clone <repository-url> robopark
cd robopark/deploy
sudo install -o root -g root -m 0600 host.env.example host.env
sudoedit host.env
```

The wrapper rejects `host.env` unless it is a root-owned regular file with mode
exactly `0600`; do not edit it as an unprivileged user or relax its permissions.

| Variable | Notes |
|----------|--------|
| `SEED_PASSWORD` | Strong password for first admin (`royal`) |
| `OPERATOR_SHARED_PASSWORD` | Optional; empty disables self-register |
| `CORS_ORIGINS` | Exact HTTPS origin users open, e.g. `https://robopark.<region>.tuna.am` |
| `COOKIE_SECURE` | **`true`** — Tuna terminates TLS |
| `SECRET_KEY` | Required for encrypting Tracker / Emergency secrets at rest |
| `UVICORN_WORKERS` | API processes inside the one `api` container (default **2**, cap **4**) |

The installer creates database credentials automatically. For the supported direct
Compose path, use the root-run wrapper. It creates and validates the same external
root-private files, then passes only their paths to Compose without sourcing a
root-only file into an unprivileged shell:

```sh
sudo ./compose-production.sh up -d --build --wait
```

The command creates `/etc/robopark/postgres-password` as root `0600` and
`/etc/robopark/pgpass` as UID/GID 10001 `0600`. It preserves and validates existing
credentials and never writes a password into the checkout or the generated env file.
Do not create `deploy/postgres-password` or relax either file to mode `0644`.

Start the stack (web listens on **localhost only**):

```sh
sudo ./compose-production.sh up -d --build --wait
```

Validate locally:

```sh
sudo ./compose-production.sh config --quiet
sudo ./compose-production.sh ps
sudo ./compose-production.sh logs --tail=100 api web
curl -sS http://127.0.0.1:8080/api/health
```

- SPA + `/api` proxy: **`http://127.0.0.1:8080`** on the host.
- API is **not** published on port 8000 — only via the web container.
- PostgreSQL 17 lives in the `robopark_postgres` Docker volume and is bound to
  `127.0.0.1:5432` only.

## 2. Tuna tunnel (public HTTPS link)

### Account and client

1. Register at [tuna.am](https://tuna.am/) and create a token in the cabinet.
2. Install **tuna-cli** (or Desktop) from [Install docs](https://tuna.am/docs/guides/install/) / [releases](https://tuna.am/releases/).
3. Prefer a **Russian region** (`--location` / `TUNA_LOCATION`) for ping and stability — see current locations in the Tuna UI / `tuna http --help`.

Paid plans can reserve a stable subdomain (`--subdomain`) or attach a custom domain (`--domain`).  
Free plan: dynamic name, **~30 minutes** per tunnel — fine for demos, not for 24/7 production.

### One-shot (test / free plan)

Free plan: dynamic hostname, **~30 minutes**. After reconnect the URL changes —
update `CORS_ORIGINS` and restart the API container.

```sh
set -a; . /etc/robopark/tuna.env; set +a   # do not paste the token into shell history
tuna http 127.0.0.1:8080 --https-redirect --qr
```

### Production (paid: reserved subdomain)

```sh
tuna http 127.0.0.1:8080 --subdomain=robopark --https-redirect
```

Copy the printed **HTTPS** URL into `CORS_ORIGINS`, restart API if needed:

```sh
# in host.env
CORS_ORIGINS=https://robopark.<region>.tuna.am
sudo ./compose-production.sh up -d api
```

Mechanics open that HTTPS link — no VPN app.

### Production: systemd unit

On the host, after installing `tuna` on `$PATH`:

```sh
sudo mkdir -p /etc/robopark
sudo cp tuna.env.example /etc/robopark/tuna.env
sudo chmod 600 /etc/robopark/tuna.env
# edit: TUNA_TOKEN, TUNA_BIN if needed, TUNA_SUBDOMAIN / TUNA_LOCATION / TUNA_DOMAIN
command -v tuna   # expect e.g. /usr/local/bin/tuna

sudo install -m 755 tuna-http.sh /etc/robopark/tuna-http.sh
sudo cp tuna.service /etc/systemd/system/robopark-tuna.service
sudo systemctl daemon-reload
sudo systemctl enable --now robopark-tuna
sudo systemctl status robopark-tuna
journalctl -u robopark-tuna -f
```

Ensure Compose starts on boot (`restart: unless-stopped` plus the provided host
systemd unit). Do not create a second cron-based launcher.

Files: `tuna.service`, `tuna.env.example`, `tuna-http.sh`.

- `--https-redirect` is always passed by the wrapper  
- bind `127.0.0.1:8080` — do not expose 8080 on WAN  
- optional `TUNA_RATE_LIMIT` in the env file  
- do **not** enable Tuna `--cors` if Robopark already sends CORS (`CORS_ORIGINS`)  
- copy the **HTTPS** URL into `CORS_ORIGINS`, then run
  `sudo ./compose-production.sh up -d api`

## 3. Firewall

- **Do not** publish `8080` / `8000` to the internet. Compose binds `127.0.0.1:8080`.
- Allow outbound HTTPS from the host so the Tuna agent can reach Tuna.
- Keep LAN access to `127.0.0.1:8080` only for admin debugging.

## 4. Security notes

| Layer | What protects it |
|-------|------------------|
| Browser ↔ Tuna | HTTPS (TLS) |
| Tuna ↔ host | Encrypted agent tunnel |
| App auth | Session cookie (`COOKIE_SECURE=true`), login throttle |
| Secrets at rest | `SECRET_KEY` (Fernet) for Tracker / Emergency |

Tuna makes the app reachable by **URL**. Rely on Robopark login; rotate `TUNA_TOKEN` if leaked; use a reserved subdomain or custom domain in production. Set **`SECRET_KEY`** in `host.env` before first boot — without it the API refuses to persist Tracker and Emergency secrets (`MissingSecretKeyError`).

Login throttle and audit IPs depend on Tuna forwarding `X-Forwarded-For` / `X-Forwarded-Proto`; nginx trusts those headers from `127.0.0.1` (the agent).

Optional Tuna extras (cabinet / docs): basic-auth / key-auth in front of the app, CIDR allowlists, [Apps / Zero Trust](https://tuna.am/docs/apps/) — not required for the default Robopark flow.

## Backup

Royal (owner) can take a full snapshot and restore it from **Администрирование → Снимок и обновление**.
The archive is a ZIP with a `snapshot` manifest: a PostgreSQL custom-format dump,
data files, and the permitted configuration projection.

To move to another host: install and start Compose once, sign in as royal, restore the ZIP, type `ВОССТАНОВИТЬ`. Keep the same `SECRET_KEY` or re-enter Tracker/Emergency secrets.

Do **not** commit `host.env`, `tuna.env`, or Tuna tokens.

## Updates

### First upgrade to the secret-safe ops-agent

The first upgrade containing the secret-safe ops-agent **must be a protected,
manual operation on the host**. Do not send this release through the Royal ZIP
flow while the old fallback agent is still running. Before starting, make an
access-restricted host backup of `deploy/host.env` outside the checkout and
verify that the backup exists. Do not print or paste the file contents into
commands or logs.

Run these commands from the Robopark checkout root on the host:

```sh
test -s deploy/host.env
sudo deploy/compose-production.sh stop ops-agent
git pull --ff-only origin main
test -s deploy/host.env
sudo deploy/compose-production.sh up -d --build --force-recreate --wait \
  --wait-timeout 180 api web ops-agent
```

After this manual bootstrap succeeds, later Royal ZIP updates can use the fixed
current ops-agent.

### Release verification

[`../scripts/verify.sh`](../scripts/verify.sh) is the single source of truth for
local and CI verification. From the checkout root, run the focused gates while
developing and the canonical full gate on a Docker-capable host before a host
upgrade or release ZIP:

```sh
./scripts/verify.sh api
./scripts/verify.sh web
./scripts/verify.sh       # canonical full gate; requires Docker
```

Update API dependencies deliberately with `cd apps/api && uv lock && uv lock
--check`, then run the canonical full gate. Base-image digest updates are also
deliberate changes and require the full gate to pass before release.

Pack a *release* ZIP on a machine with the repo (not a snapshot):

```sh
chmod +x scripts/pack-release.sh
scripts/pack-release.sh ./robopark-release.zip
```

Royal uploads that ZIP on the same admin tab, types `ОБНОВИТЬ`. The API checks the archive, runs tests on a copy, snapshots, then copies files onto the checkout. The `ops-agent` compose service rebuilds `api` and `web`.

## Residual risk (accepted)

* **ops-agent + `docker.sock`** — a compromised royal session (or a release that passes tests) can rewrite the checkout and rebuild containers. Keep the royal account offline-only and treat release ZIPs as trusted code.
* **Driver Emergency** — approved drivers may open any VIN without a Tracker park ticket (product: Emergency-only cabinet).
* **Session cookies** — CSRF relies on `SameSite=lax` + same-origin nginx proxy; do not loosen cookie flags without adding CSRF tokens.

## Quick reference

| Piece | Where | Command |
|-------|--------|---------|
| App | `deploy/` | `sudo ./compose-production.sh up -d --build --wait` |
| Tunnel | host systemd | `systemctl enable --now robopark-tuna` |
| Users | browser | `https://<your-tuna-host>/` |

## What we removed

WireGuard VPS hub (`deploy/vps/`, `deploy/wireguard/`) is gone. Access is HTTPS via Tuna only.
