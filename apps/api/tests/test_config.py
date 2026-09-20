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
