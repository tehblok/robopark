import pytest
from pydantic import ValidationError

from robopark_api.config import Settings


def test_ip_geo_provider_is_disabled_by_default():
    assert Settings(_env_file=None).ip_geo_provider == "off"


def test_ip_geo_provider_accepts_explicit_environment_opt_in(monkeypatch):
    monkeypatch.setenv("IP_GEO_PROVIDER", "ipwhois")
    assert Settings(_env_file=None).ip_geo_provider == "ipwhois"


def test_ip_geo_provider_rejects_unknown_value():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ip_geo_provider="unexpected")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("password_min_length", 0),
        ("login_max_attempts", 0),
        ("register_max_attempts", 0),
        ("login_attempt_window_seconds", 0),
        ("login_lockout_seconds", 0),
        ("session_idle_seconds", 0),
        ("session_absolute_ttl_seconds", 0),
        ("session_slide_min_interval_seconds", -1),
        ("cookie_samesite", "bogus"),
    ],
)
def test_security_settings_reject_unsafe_values(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})
