def test_admin_sees_only_latest_activity_without_exposing_it_in_profile(client, seed_royal):
    headers = {
        "x-real-ip": "203.0.113.11",
        "user-agent": "Mozilla/5.0 (Linux; Android 15) Chrome/130.0",
    }
    response = client.post(
        "/auth/login",
        json={"username": "royal", "password": "secret"},
        headers=headers,
    )
    assert response.status_code == 204
    profile = client.get("/auth/me", headers=headers).json()
    assert "last_ip" not in profile

    user = next(
        row
        for row in client.get("/admin/users", headers=headers).json()
        if row["id"] == seed_royal.id
    )
    assert user["last_ip"] == "203.0.113.11"
    assert user["last_device"] == "Android · Chrome"
    assert user["last_seen_at"] is not None
    assert user["last_location"] is None


def test_disabled_geo_provider_never_performs_http_lookup(monkeypatch):
    from robopark_api.config import Settings
    from robopark_api.services import ip_location

    calls = []
    monkeypatch.setattr(
        ip_location,
        "get_settings",
        lambda: Settings(_env_file=None),
    )
    monkeypatch.setattr(ip_location.httpx, "get", lambda *args, **kwargs: calls.append(args))
    assert ip_location.lookup_public_ip("8.8.8.8") is None
    assert calls == []


def test_private_ip_never_reaches_explicit_geo_provider(monkeypatch):
    from robopark_api.services import ip_location

    calls = []
    monkeypatch.setattr(ip_location.httpx, "get", lambda *args, **kwargs: calls.append(args))
    assert ip_location.lookup_public_ip("192.168.1.5", provider="ipwhois") is None
    assert calls == []


def test_activity_is_throttled_but_device_changes_are_recorded(db_session, seed_royal):
    from robopark_api.services.user_activity import record_activity

    assert (
        record_activity(db_session, seed_royal, ip="192.168.1.5", user_agent="Chrome/130.0") is None
    )
    first = seed_royal.last_seen_at
    assert (
        record_activity(db_session, seed_royal, ip="192.168.1.5", user_agent="Chrome/130.0") is None
    )
    assert seed_royal.last_seen_at == first
    record_activity(db_session, seed_royal, ip="192.168.1.5", user_agent="Firefox/130.0")
    assert seed_royal.last_device.endswith("Firefox")


def test_public_lookup_returns_approximate_place_and_timeout_is_nonfatal(monkeypatch):
    import httpx

    from robopark_api.services import ip_location

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"success": True, "city": "Москва", "region": "Москва", "country": "Россия"}

    request_options = {}

    def successful_get(*args, **kwargs):
        request_options.update(kwargs)
        return Response()

    monkeypatch.setattr(ip_location.httpx, "get", successful_get)
    assert (
        ip_location.lookup_public_ip("8.8.8.8", provider="ipwhois")
        == "Москва, Москва, Россия"
    )
    assert request_options == {"timeout": 2.0, "follow_redirects": False}
    monkeypatch.setattr(
        ip_location.httpx,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(httpx.TimeoutException("slow")),
    )
    assert ip_location.lookup_public_ip("8.8.8.8", provider="ipwhois") is None


def test_disabled_provider_skips_quota_cache_and_network(monkeypatch):
    from robopark_api.config import Settings
    from robopark_api.services import ip_location

    monkeypatch.setattr(
        ip_location,
        "get_settings",
        lambda: Settings(_env_file=None, ip_geo_provider="off"),
    )
    monkeypatch.setattr(
        ip_location,
        "_reserve_lookup",
        lambda: (_ for _ in ()).throw(AssertionError("quota must not be reserved")),
    )
    monkeypatch.setattr(
        ip_location.httpx,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network must stay off")),
    )

    assert ip_location.resolve_for_user(1, "8.8.8.8") is None


def test_geo_lookup_quota_is_shared_in_database(db_session, monkeypatch):
    from datetime import UTC, datetime

    from sqlalchemy.orm import sessionmaker

    from robopark_api.models import IpGeoQuota
    from robopark_api.services import ip_location

    monkeypatch.setattr(ip_location, "SessionLocal", sessionmaker(bind=db_session.bind))
    today = datetime.now(UTC).date().isoformat()
    db_session.add(IpGeoQuota(day=today, count=ip_location.LOOKUPS_PER_DAY))
    db_session.commit()
    assert ip_location._reserve_lookup() is False
