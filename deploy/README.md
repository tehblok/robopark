# Deployment: host + WireGuard (VPS)

Robopark runs **only on the host** (Armbian / home server): API, SQLite, web UI.

The **VPS runs WireGuard only** — no Robopark containers, no Caddy, no tunnel to
publish the app on the public internet. Operators connect to the VPN, then open
the site on the host's VPN address.

```
  Operator laptop/phone          VPS (public IP)              Host (no white IP)
  ┌─────────────────┐           ┌──────────────┐            ┌──────────────────┐
  │ WireGuard client│◄──UDP───►│ WireGuard    │◄───VPN────►│ wg + Docker      │
  │                 │   51820   │ hub (Docker) │   tunnel   │ api + web :8080  │
  └────────┬────────┘           └──────────────┘            └──────────────────┘
           │                                                          ▲
           └──────── HTTP http://10.8.0.2:8080 (host peer IP) ────────┘
```

Default VPN subnet: `10.8.0.0/24` — server `.1`, host peer `.2`, operators `.3+`.

## 1. Host (application)

On the machine that runs 24/7 (Armbian, NUC, etc.):

```sh
git clone <repository-url> robopark
cd robopark/deploy
cp host.env.example host.env
```

Edit `host.env`:

| Variable | Notes |
|----------|--------|
| `SEED_PASSWORD` | Strong password for first admin (`royal`) |
| `OPERATOR_SHARED_PASSWORD` | Optional; empty disables self-register |
| `CORS_ORIGINS` | Must match how users open the UI, e.g. `http://10.8.0.2:8080` |
| `COOKIE_SECURE` | Keep `false` for VPN over HTTP (see below) |

Start the stack:

```sh
export HOST_ENV_FILE=./host.env
docker compose up -d --build
```

- Web (SPA + `/api` proxy): **`http://<host-vpn-ip>:8080`** (default `http://10.8.0.2:8080` after WireGuard is up).
- API is **not** published on port 8000 — only reachable via the web container's nginx proxy.
- SQLite lives in Docker volume `robopark_data`.

Validate:

```sh
docker compose ps
curl -sS http://127.0.0.1:8080/api/health
```

### Session cookies over VPN

Traffic is **HTTP inside WireGuard** (no TLS terminator). `COOKIE_SECURE=false`
is required so the browser stores the session cookie. The VPN replaces TLS for
transport privacy among trusted peers.

## 2. VPS (WireGuard hub)

On a small Ubuntu VPS with a public IP:

```sh
git clone <repository-url> robopark
cd robopark/deploy/vps
cp wg.env.example wg.env
```

Edit `wg.env`: set `SERVERURL` to the VPS public IP or DNS, adjust `PEERS` (first
name **`host`** is reserved for the application server).

Open **UDP `51820`** (or your `SERVERPORT`) in the VPS firewall / security group.

```sh
docker compose --env-file wg.env up -d
```

Peer configs are generated under `./config/peer_<name>/`:

| Peer | Role | Typical VPN IP |
|------|------|----------------|
| `host` | Armbian / home server | `10.8.0.2` |
| `operator1`, … | Staff devices | `10.8.0.3`, … |

Details: [`vps/README.md`](vps/README.md).

## 3. Host WireGuard client

Copy `config/peer_host/` from the VPS to the host (scp/rsync). On Armbian:

```sh
sudo apt install wireguard
sudo cp peer_host/robopark-host.conf /etc/wireguard/wg0.conf
sudo chmod 600 /etc/wireguard/wg0.conf
sudo systemctl enable --now wg-quick@wg0
```

Confirm the host has `10.8.0.2` on `wg0` and can ping `10.8.0.1` (VPS).

From a machine already on the VPN:

```sh
curl -sS http://10.8.0.2:8080/api/health
```

Step-by-step: [`wireguard/host-peer.md`](wireguard/host-peer.md).

## 4. Operator devices

Distribute `config/peer_operatorN/` **securely** (not in chat/email if avoidable).
Import the `.conf` or QR code into the WireGuard app, connect, open:

**http://10.8.0.2:8080**

(`CORS_ORIGINS` in `host.env` must include this origin.)

## 5. Firewall (recommended)

**Host:** allow `8080/tcp` only on `wg0` (or from `10.8.0.0/24`), not on the public LAN/WAN interface.

**VPS:** allow `51820/udp` from the internet; no need to expose 80/443 for Robopark.

## Backup

Back up the `robopark_data` volume / SQLite file on the host regularly. WireGuard
`config/` on the VPS contains private keys — back up encrypted, never commit.

## What we removed

Earlier drafts used a **VPS Caddy + reverse tunnel** to publish HTTPS on a public
domain. That path is replaced by **VPN-only access**. See git history under
`deploy/` if you need the old templates.

## Quick reference

| Machine | Compose path | Command |
|---------|--------------|---------|
| Host | `deploy/` | `docker compose up -d --build` |
| VPS | `deploy/vps/` | `docker compose --env-file wg.env up -d` |

Never run `deploy/docker-compose.yml` on the VPS.
