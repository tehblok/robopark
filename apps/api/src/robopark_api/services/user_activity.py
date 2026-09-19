"""A single last-seen record per user, throttled for busy pages."""

import ipaddress
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from robopark_api.models import User


def public_ip(raw: str | None) -> str | None:
    try:
        address = ipaddress.ip_address((raw or "").strip())
    except ValueError:
        return None
    return str(address) if address.is_global else None


def observed_ip(raw: str | None) -> str | None:
    try:
        return str(ipaddress.ip_address((raw or "").strip()))
    except ValueError:
        return None


def device_label(raw: str | None) -> str:
    agent = (raw or "")[:512].lower()
    platform = (
        "Android"
        if "android" in agent
        else "iPhone"
        if "iphone" in agent
        else "iPad"
        if "ipad" in agent
        else "Windows"
        if "windows" in agent
        else "Mac"
        if "macintosh" in agent
        else "Linux"
        if "linux" in agent
        else "Устройство не определено"
    )
    browser = (
        "Edge"
        if "edg/" in agent
        else "Chrome"
        if "chrome/" in agent or "chromium/" in agent
        else "Firefox"
        if "firefox/" in agent
        else "Safari"
        if "safari/" in agent
        else ""
    )
    return f"{platform} · {browser}" if browser else platform


def record_activity(
    db: Session, user: User, *, ip: str | None, user_agent: str | None
) -> str | None:
    """Update only after ten minutes or on an address/device change; return new public IP."""
    now = datetime.now(UTC)
    address = observed_ip(ip)
    device = device_label(user_agent)
    prior = user.last_seen_at
    if prior is not None and prior.tzinfo is None:
        prior = prior.replace(tzinfo=UTC)
    changed_ip = user.last_ip != address
    changed_device = user.last_device != device
    if not changed_ip and not changed_device and prior and now - prior < timedelta(minutes=10):
        return None
    user.last_seen_at = now
    user.last_ip = address
    user.last_device = device
    if changed_ip:
        user.last_location = None
    db.flush()
    return public_ip(address) if changed_ip or not user.last_location else None
