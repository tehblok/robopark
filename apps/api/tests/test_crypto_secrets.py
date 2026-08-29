"""Encryption at rest for integration secrets."""

import pytest

from robopark_api import crypto
from robopark_api.config import Settings, reset_settings_cache
from robopark_api.models import PlatformSetting
from robopark_api.services import platform_settings as settings_svc


@pytest.fixture
def with_secret_key(monkeypatch):
    """Configure a master key for the duration of a test."""
    key = "unit-test-master-key"
    monkeypatch.setenv("SECRET_KEY", key)
    reset_settings_cache()
    yield key
    reset_settings_cache()


def test_roundtrip():
    key = crypto.generate_secret_key()
    encrypted = crypto.encrypt_secret("s3cret-token", key)

    assert encrypted.startswith(crypto.ENC_PREFIX)
    assert "s3cret-token" not in encrypted
    assert crypto.decrypt_secret(encrypted, key) == "s3cret-token"


def test_wrong_key_is_rejected():
    encrypted = crypto.encrypt_secret("value", crypto.generate_secret_key())
    with pytest.raises(crypto.SecretDecryptionError):
        crypto.decrypt_secret(encrypted, crypto.generate_secret_key())


def test_missing_key_refuses_encrypt():
    """Without SECRET_KEY, secret writes fail closed."""
    with pytest.raises(crypto.MissingSecretKeyError):
        crypto.encrypt_secret("value", None)
    assert crypto.decrypt_secret("value", None) == "value"


def test_token_is_encrypted_in_database(db_session, with_secret_key):
    settings_svc.set_setting(db_session, settings_svc.TRACKER_TOKEN_KEY, "oauth-token")

    raw = db_session.get(PlatformSetting, settings_svc.TRACKER_TOKEN_KEY)
    assert raw.value.startswith(crypto.ENC_PREFIX)
    assert "oauth-token" not in raw.value

    assert settings_svc.get_tracker_token(db_session) == "oauth-token"


def test_emergency_cookie_is_encrypted(db_session, with_secret_key):
    settings_svc.set_setting(db_session, settings_svc.EMERGENCY_COOKIE_KEY, "Session_id=abc")

    raw = db_session.get(PlatformSetting, settings_svc.EMERGENCY_COOKIE_KEY)
    assert "Session_id" not in raw.value
    assert settings_svc.get_emergency_cookie(db_session) == "Session_id=abc"


def test_legacy_plaintext_value_still_readable(db_session, with_secret_key):
    """An existing database written before encryption must keep working."""
    db_session.add(PlatformSetting(key=settings_svc.TRACKER_TOKEN_KEY, value="legacy-plain-token"))
    db_session.commit()

    assert settings_svc.get_tracker_token(db_session) == "legacy-plain-token"

    # Re-saving upgrades it to ciphertext.
    settings_svc.set_setting(db_session, settings_svc.TRACKER_TOKEN_KEY, "legacy-plain-token")
    raw = db_session.get(PlatformSetting, settings_svc.TRACKER_TOKEN_KEY)
    assert raw.value.startswith(crypto.ENC_PREFIX)


def test_rotated_key_does_not_crash_api(db_session, monkeypatch):
    """A changed SECRET_KEY must degrade to 'not configured', not a 500."""
    monkeypatch.setenv("SECRET_KEY", "original-key")
    reset_settings_cache()
    settings_svc.set_setting(db_session, settings_svc.TRACKER_TOKEN_KEY, "token")

    monkeypatch.setenv("SECRET_KEY", "rotated-key")
    reset_settings_cache()
    assert settings_svc.get_tracker_token(db_session) is None


def test_masking_uses_plaintext_not_ciphertext(db_session, with_secret_key):
    settings_svc.set_setting(db_session, settings_svc.TRACKER_TOKEN_KEY, "abcdefgh")

    status = settings_svc.integration_status(db_session)
    assert status["tracker_token_masked"] == "•••• (8)"
    assert status["tracker_token_encrypted"] is True


def test_non_secret_settings_stay_plaintext(db_session, with_secret_key):
    """Only real secrets are encrypted; operational flags stay readable."""
    settings_svc.set_bool_setting(db_session, settings_svc.TRACKER_MECHANIC_WRITE_KEY, False)
    raw = db_session.get(PlatformSetting, settings_svc.TRACKER_MECHANIC_WRITE_KEY)
    assert raw.value == "false"


def test_settings_cache_is_effective():
    reset_settings_cache()
    from robopark_api.config import get_settings

    assert get_settings() is get_settings()
    reset_settings_cache()
    assert isinstance(Settings(), Settings)


def test_migrate_plaintext_secrets_reseals_legacy_rows(db_session, with_secret_key):
    """Plaintext rows from an older install must be re-encrypted on boot."""
    db_session.add(
        PlatformSetting(key=settings_svc.TRACKER_TOKEN_KEY, value="legacy-token")
    )
    db_session.add(
        PlatformSetting(key=settings_svc.EMERGENCY_COOKIE_KEY, value="Session_id=old")
    )
    db_session.commit()

    rewritten = settings_svc.migrate_plaintext_secrets(db_session)

    assert rewritten == 2
    for key, cleartext in (
        (settings_svc.TRACKER_TOKEN_KEY, "legacy-token"),
        (settings_svc.EMERGENCY_COOKIE_KEY, "Session_id=old"),
    ):
        raw = db_session.get(PlatformSetting, key)
        assert raw.value.startswith(crypto.ENC_PREFIX)
        assert cleartext not in raw.value
    # Plaintext is still readable through the accessor.
    assert settings_svc.get_tracker_token(db_session) == "legacy-token"
    assert settings_svc.get_emergency_cookie(db_session) == "Session_id=old"


def test_migrate_plaintext_secrets_is_noop_without_key(db_session, monkeypatch):
    """Without SECRET_KEY the migration must not touch anything."""
    monkeypatch.delenv("SECRET_KEY", raising=False)
    reset_settings_cache()
    db_session.add(
        PlatformSetting(key=settings_svc.TRACKER_TOKEN_KEY, value="legacy-token")
    )
    db_session.commit()

    assert settings_svc.migrate_plaintext_secrets(db_session) == 0

    raw = db_session.get(PlatformSetting, settings_svc.TRACKER_TOKEN_KEY)
    assert raw.value == "legacy-token"


def test_migrate_plaintext_secrets_skips_already_encrypted(db_session, with_secret_key):
    settings_svc.set_setting(db_session, settings_svc.TRACKER_TOKEN_KEY, "fresh-token")
    assert settings_svc.migrate_plaintext_secrets(db_session) == 0
