"""Best-effort, bounded IP location lookup outside the request critical path."""

import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError

from robopark_api.db import SessionLocal
from robopark_api.models import IpGeoCache, IpGeoQuota, User
from robopark_api.services.user_activity import public_ip

logger = logging.getLogger(__name__)
LOOKUPS_PER_DAY = 800
POSITIVE_TTL = timedelta(days=7)
NEGATIVE_TTL = timedelta(hours=1)


def lookup_public_ip(ip: str) -> str | None:
    address = public_ip(ip)
    if address is None:
        return None
    try:
        response = httpx.get(f"https://ipwho.is/{address}", timeout=2.0, follow_redirects=False)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("success") is not True:
            return None
        parts = [str(payload.get(key) or "").strip() for key in ("city", "region", "country")]
        safe = ["".join(char for char in part if char.isprintable())[:80] for part in parts]
        return ", ".join(part for part in safe if part)[:256] or None
    except (httpx.HTTPError, ValueError, TypeError):
        logger.info("Approximate IP location unavailable")
        return None


def _reserve_lookup() -> bool:
    now = datetime.now(UTC)
    today = now.date().isoformat()
    with SessionLocal() as db:
        updated = db.execute(
            update(IpGeoQuota)
            .where(IpGeoQuota.day == today, IpGeoQuota.count < LOOKUPS_PER_DAY)
            .values(count=IpGeoQuota.count + 1)
        )
        if updated.rowcount:
            db.commit()
            return True
        if db.get(IpGeoQuota, today) is not None:
            return False
        try:
            db.add(IpGeoQuota(day=today, count=1))
            db.execute(
                delete(IpGeoQuota).where(
                    IpGeoQuota.day < (now.date() - timedelta(days=7)).isoformat()
                )
            )
            db.execute(delete(IpGeoCache).where(IpGeoCache.expires_at < now))
            db.commit()
            return True
        except IntegrityError:
            db.rollback()
            return False


def resolve_for_user(user_id: int, ip: str) -> None:
    address = public_ip(ip)
    if address is None:
        return
    now = datetime.now(UTC)
    with SessionLocal() as db:
        cached = db.get(IpGeoCache, address)
        expires = cached.expires_at if cached else None
        if expires and expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        location = cached.location if expires and expires > now else None
        fresh = bool(expires and expires > now)
    if not fresh:
        if not _reserve_lookup():
            return
        location = lookup_public_ip(address)
        with SessionLocal() as db:
            cached = db.get(IpGeoCache, address)
            if cached is None:
                cached = IpGeoCache(ip=address, location=location, expires_at=now)
                db.add(cached)
            cached.location = location
            cached.expires_at = now + (POSITIVE_TTL if location else NEGATIVE_TTL)
            db.commit()
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is not None and user.last_ip == address:
            user.last_location = location
            db.commit()
