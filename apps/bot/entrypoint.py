"""Start Robopark's native Telegram service with encrypted API-owned credentials."""

from __future__ import annotations

import json
import os
import stat
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

MAX_SECRET_BYTES = 4096


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl


def read_bridge_key(path: Path) -> str:
    """Read a private regular file without following links."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) not in {0o400, 0o600}
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
                or not 0 < info.st_size <= MAX_SECRET_BYTES
            ):
                raise ValueError("unsafe_bot_bridge_key")
            value = stream.read(MAX_SECRET_BYTES + 1).strip()
    except (OSError, UnicodeError) as error:
        raise ValueError("unsafe_bot_bridge_key") from error
    if not value or len(value.encode("utf-8")) > MAX_SECRET_BYTES:
        raise ValueError("unsafe_bot_bridge_key")
    return value


def fetch_telegram_token(*, api_base_url: str, bridge_key_file: Path) -> str:
    """Fetch the encrypted-at-rest Telegram credential into process memory."""
    base = api_base_url.rstrip("/")
    parsed = urlsplit(base)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("invalid_robopark_api_url")
    request = urllib.request.Request(
        f"{base}/internal/bot/telegram-token",
        headers={"X-Robopark-Bot-Key": read_bridge_key(bridge_key_file)},
        method="GET",
    )
    try:
        with urllib.request.build_opener(_NoRedirect).open(
            request, timeout=10
        ) as response:
            length = response.headers.get("Content-Length")
            if length is not None and int(length) > MAX_SECRET_BYTES:
                raise ValueError("invalid_telegram_token_response")
            raw = response.read(MAX_SECRET_BYTES + 1)
    except (OSError, urllib.error.HTTPError, ValueError) as error:
        raise RuntimeError("telegram_token_unavailable") from error
    if len(raw) > MAX_SECRET_BYTES:
        raise RuntimeError("telegram_token_unavailable")
    try:
        payload = json.loads(raw)
        token = payload["token"].strip()
    except (
        KeyError,
        TypeError,
        AttributeError,
        UnicodeError,
        json.JSONDecodeError,
    ) as error:
        raise RuntimeError("telegram_token_unavailable") from error
    if not token or len(token) > 512:
        raise RuntimeError("telegram_token_unavailable")
    return token


def _fetch_with_retry(api_url: str, key_file: Path) -> str:
    for attempt in range(12):
        try:
            return fetch_telegram_token(
                api_base_url=api_url,
                bridge_key_file=key_file,
            )
        except RuntimeError:
            if attempt == 11:
                raise
            time.sleep(5)
    raise RuntimeError("telegram_token_unavailable")


def open_runtime_heartbeat(data_dir: Path) -> int:
    """Keep one private inode updated while the bot process is alive."""
    descriptor = os.open(
        data_dir / "runtime-heartbeat",
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise OSError("unsafe_bot_heartbeat")
        os.fchmod(descriptor, 0o600)
        os.ftruncate(descriptor, 0)
        os.write(descriptor, b"1\n")
        os.fsync(descriptor)
        # Startup is not readiness. Only the healthy runtime renews this marker.
        os.utime(descriptor, (0, 0))
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def main() -> int:
    from native.runtime import run_service

    data_dir = Path(os.environ.get("ROBOPARK_BOT_DATA_DIR", "/data/telegram-bot"))
    api_url = os.environ.get("ROBOPARK_API_URL", "http://api:8000")
    key_file = Path(
        os.environ.get("ROBOPARK_BOT_BRIDGE_KEY_FILE", "/run/secrets/bot-bridge-key")
    )
    ready_file = Path("/tmp/bot-ready")
    ready_file.unlink(missing_ok=True)
    data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    token = _fetch_with_retry(api_url, key_file)
    heartbeat_fd = open_runtime_heartbeat(data_dir)
    try:
        return run_service(
            data_dir=data_dir,
            api_url=api_url,
            bridge_key=read_bridge_key(key_file),
            token=token,
            ready_file=ready_file,
            heartbeat_fd=heartbeat_fd,
        )
    finally:
        ready_file.unlink(missing_ok=True)
        os.close(heartbeat_fd)


if __name__ == "__main__":
    raise SystemExit(main())
