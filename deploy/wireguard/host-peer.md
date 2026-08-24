# Host WireGuard peer (Armbian / Linux)

The application host joins the VPS WireGuard hub as peer **`host`**, usually
address **`10.8.0.2/32`**. Operators reach Robopark at `http://10.8.0.2:8080`
while connected to the same VPN.

## 1. Get the peer config from the VPS

After `docker compose up` in `deploy/vps/`:

```sh
# on VPS
ls config/peer_host/
# peer_host.png  peer_host.conf  (names may vary slightly)
```

Copy `peer_host.conf` to the host (scp, USB, etc.).

## 2. Install WireGuard on the host

```sh
sudo apt update
sudo apt install wireguard
```

## 3. Install the config

```sh
sudo cp peer_host.conf /etc/wireguard/wg0.conf
sudo chmod 600 /etc/wireguard/wg0.conf
```

Ensure the `[Interface]` section includes:

```ini
[Interface]
Address = 10.8.0.2/32
PrivateKey = <from generated config>
# Optional: DNS = 1.1.1.1
```

And `[Peer]` points at the VPS:

```ini
[Peer]
PublicKey = <server public key>
Endpoint = <SERVERURL>:51820
AllowedIPs = 10.8.0.0/24
PersistentKeepalive = 25
```

(`AllowedIPs` may differ if you changed `ALLOWEDIPS` in `wg.env`.)

## 4. Enable on boot

```sh
sudo systemctl enable wg-quick@wg0
sudo systemctl start wg-quick@wg0
sudo wg show
```

You should see `10.8.0.2` on interface `wg0`.

## 5. Start Robopark (if not already)

```sh
cd robopark/deploy
export HOST_ENV_FILE=./host.env
docker compose up -d --build
```

## 6. Verify end-to-end

On the host:

```sh
curl -sS http://127.0.0.1:8080/api/health
curl -sS http://10.8.0.2:8080/api/health
```

From an operator device on VPN:

- Open **http://10.8.0.2:8080**
- Log in; session cookie requires `COOKIE_SECURE=false` in `host.env`

## 7. Host firewall (optional, recommended)

Restrict port 8080 to the VPN subnet only (example ufw):

```sh
sudo ufw allow in on wg0 to any port 8080 proto tcp
sudo ufw deny 8080/tcp
```

Adjust if you also need LAN access without VPN.

## Troubleshooting

| Symptom | Check |
|---------|--------|
| No `wg0` / handshake | VPS UDP 51820 open; `SERVERURL` correct; host behind NAT needs `PersistentKeepalive` |
| VPN up, curl to .2 fails | Docker running? `docker compose ps`; port 8080 listening `ss -lntp \| grep 8080` |
| Login loops | `COOKIE_SECURE=false`; `CORS_ORIGINS` includes `http://10.8.0.2:8080` |
| Tracker tools 503 | Set Tracker token in `/admin` on host |
