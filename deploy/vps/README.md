# WireGuard on the VPS (Robopark hub)

This directory runs **only WireGuard**. Robopark API, web, and SQLite stay on
the host — see [`../README.md`](../README.md).

## Prerequisites

- Ubuntu 22.04+ (or similar) VPS with a **public IPv4**
- Docker Engine + Compose plugin
- UDP port **51820** open inbound (or the port in `wg.env`)

## First-time setup

```sh
cd robopark/deploy/vps
cp wg.env.example wg.env
```

Edit `wg.env`:

| Variable | Purpose |
|----------|---------|
| `SERVERURL` | VPS public IP or DNS name (what clients put in `Endpoint`) |
| `SERVERPORT` | WireGuard listen port (default 51820) |
| `PEERS` | Comma-separated names; **`host` first** = application server |
| `INTERNAL_SUBNET` | Default `10.8.0.0` → server `.1`, peers `.2+` |
| `ALLOWEDIPS` | `10.8.0.0/24` = split tunnel (only VPN traffic via WG) |

Start:

```sh
docker compose --env-file wg.env up -d
docker compose logs -f wireguard
```

Generated configs: `./config/peer_<name>/`

- `peer_host/` → copy to the Armbian/home server
- `peer_operator1/` → give to an operator (phone/laptop WireGuard app)

## Adding peers later

1. Stop the container.
2. Append a new name to `PEERS` in `wg.env` (comma-separated).
3. Remove `./config/` **or** use linuxserver's documented regen flow if you need
   to preserve existing keys — simplest for greenfield: edit `PEERS` before first
   `up`; to add later, add `peer_operator3` manually or recreate with backup.

For production, prefer backing up `config/` before changing `PEERS`, then follow
[linuxserver/wireguard](https://github.com/linuxserver/docker-wireguard) docs for
adding peers without rotating server keys.

## Firewall (ufw example)

```sh
sudo ufw allow 51820/udp
sudo ufw enable
```

Do **not** open 80/443 unless you run other services on this VPS.

## Health checks

On the VPS:

```sh
docker exec robopark-wireguard wg show
```

From a connected client:

```sh
ping 10.8.0.1    # VPS VPN address
ping 10.8.0.2    # host (after host peer is up)
curl http://10.8.0.2:8080/api/health
```

## Security notes

- Treat `./config/` as **secret** (private keys). It is gitignored.
- Rotate peer configs when a device is lost.
- `ALLOWEDIPS=10.8.0.0/24` avoids routing all operator internet traffic through your VPS.
