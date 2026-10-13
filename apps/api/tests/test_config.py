import pytest
from pydantic import ValidationError

from robopark_api.config import Settings


def test_bridge_requires_server_identity_and_hmac_secret():
    with pytest.raises(ValueError, match="emergency_bridge_identity_required"):
        Settings(_env_file=None, emergency_bridge_enabled=True, secret_key="secret")
    with pytest.raises(ValueError, match="secret_key_required"):
        Settings(
            _env_file=None,
            emergency_bridge_enabled=True,
            robopark_instance_id="11111111-1111-1111-1111-111111111111",
            secret_key=None,
        )


def test_ip_geo_provider_is_disabled_by_default():
    assert Settings(_env_file=None).ip_geo_provider == "off"


def test_ip_geo_provider_accepts_explicit_environment_opt_in(monkeypatch):
    monkeypatch.setenv("IP_GEO_PROVIDER", "ipwhois")
    assert Settings(_env_file=None).ip_geo_provider == "ipwhois"


def test_ip_geo_provider_rejects_unknown_value():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ip_geo_provider="unexpected")


def test_ai_chat_workers_defaults_to_four_and_is_bounded():
    assert Settings(_env_file=None).ai_chat_workers == 4
    assert Settings(_env_file=None, ai_chat_workers=1).ai_chat_workers == 1
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ai_chat_workers=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ai_chat_workers=5)


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
