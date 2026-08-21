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

## VPS

Copy `vps.env.example` to the untracked `vps.env`, replace the public hostname and tunnel upstream, then run:

```sh
export ROBOPARK_ROLE=vps
export VPS_ENV_FILE=./vps.env
docker compose --profile vps up -d
```

`Caddyfile.vps.example` proxies to the private tunnel address. `tunnel.env.example` lists placeholders for Cloudflare Tunnel, frp, and WireGuard; no tunnel sidecar is enabled by this Compose file.

Never enable both profiles on the same machine. Validate either role with:

```sh
docker compose --profile host config >/dev/null
docker compose --profile vps config >/dev/null
```
