# Deployment roles

This Compose project has two mutually exclusive runtime roles:

- `host`: builds and runs `api` plus `web`; the API owns the `robopark_data` SQLite volume. nginx serves the SPA on port 8080 and forwards `/api/` to the API container.
- `vps`: runs only Caddy and its own TLS/config volumes. It neither starts the application containers nor mounts `robopark_data`.

`ROBOPARK_ROLE` is a human-facing marker in the role environment files. Compose activation is controlled by `--profile`; keep both values aligned.

## Host

Copy `host.env.example` to the untracked `host.env`, set production values, then run:

```sh
export ROBOPARK_ROLE=host
export HOST_ENV_FILE=./host.env
docker compose --profile host up -d --build
```

For a local template-only check, omit `HOST_ENV_FILE`.

`host.env.example` ships `COOKIE_SECURE=true`, so the session cookie is only sent
over HTTPS. A browser will silently refuse to store it when you reach the stack
over plain `http://<host>:8080`, which looks like a login that returns 204 and
then stays logged out. Set `COOKIE_SECURE=false` in `host.env` for LAN-only
testing, and back to `true` before publishing through the VPS.

## VPS

Copy `vps.env.example` to the untracked `vps.env`, replace the public hostname and tunnel upstream, then run:

```sh
export ROBOPARK_ROLE=vps
export VPS_ENV_FILE=./vps.env
docker compose --profile vps up -d
```

`Caddyfile.vps.example` proxies to the private tunnel address. `tunnel.env.example` lists placeholders for Cloudflare Tunnel, frp, and WireGuard; no tunnel sidecar is enabled by this Compose file.

### The tunnel must expose the web container, not the API

`TUNNEL_UPSTREAM` has to resolve to the host's **web** container on port **8080**
(default `host.tunnel:8080`). That container serves the SPA and owns the `/api/`
prefix contract, stripping the prefix before forwarding to `api:8000`.

Pointing the tunnel at the API container's port 8000 instead publishes FastAPI
directly: `/` and `/index.html` return 404 because the API serves no static
files, and every SPA request to `/api/auth/me` returns 404 because the API
mounts its routes without an `/api` prefix. Only the 8080 edge satisfies both
halves of the contract.

Never enable both profiles on the same machine. Validate either role with:

```sh
docker compose --profile host config >/dev/null
docker compose --profile vps config >/dev/null
```
