from __future__ import annotations

import json
import os
import secrets
import stat
from pathlib import Path

import pytest
from robopark_ota.credentials import (
    RoyalCredentials,
    TunaConfiguration,
    collect_royal_credentials,
    collect_tuna_configuration,
    credential_file,
    seed_command,
    write_tuna_configuration,
)


def test_credentials_require_matching_password_and_product_policy():
    passwords = iter(["Short", "Short"])
    with pytest.raises(ValueError, match="royal_password_policy"):
        collect_royal_credentials(
            input_fn=lambda _prompt: "royal",
            getpass_fn=lambda _prompt: next(passwords),
        )

    passwords = iter(["Correct-Password-42!", "Different-Password-42!"])
    with pytest.raises(ValueError, match="royal_password_mismatch"):
        collect_royal_credentials(
            input_fn=lambda _prompt: "royal",
            getpass_fn=lambda _prompt: next(passwords),
        )


def test_credentials_are_hidden_and_secret_file_is_deleted(tmp_path: Path):
    password = "Correct-Password-42!"
    passwords = iter([password, password])
    credentials = collect_royal_credentials(
        input_fn=lambda _prompt: "royal",
        getpass_fn=lambda _prompt: next(passwords),
    )

    assert password not in repr(credentials)
    with credential_file(credentials, tmp_path) as path:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert json.loads(path.read_text()) == {
            "username": "royal",
            "password": password,
            "role": "royal",
        }
        command = seed_command(path)
        assert password not in " ".join(command)
        assert password not in json.dumps(dict(os.environ))
    assert not path.exists()


def test_seed_command_uses_fixed_container_path_not_password(tmp_path: Path):
    host_path = tmp_path / "credential file.json"
    value = RoyalCredentials(username="owner", password="Strong-Password-42!", role="royal")
    with credential_file(value, tmp_path) as path:
        command = seed_command(path)

    assert command[-3:] == [
        "python",
        "-m",
        "robopark_api.seed_from_file",
    ]
    assert f"{host_path}:/run/robopark/seed.json:ro" not in command
    assert f"{path}:/run/robopark/seed.json:ro" in command
    assert command[command.index("--user") + 1] == "0:0"


def test_tuna_token_is_hidden_and_written_only_to_private_host_file(tmp_path: Path):
    token = "tt_private_value"
    configuration = collect_tuna_configuration(
        getpass_fn=lambda _prompt: token,
        input_fn=lambda _prompt: "robopark.ru.tuna.am",
    )

    assert configuration == TunaConfiguration(
        token=token,
        subdomain="robopark",
        location="ru",
    )
    assert token not in repr(configuration)
    target = write_tuna_configuration(tmp_path, configuration)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert target.read_text() == (
        "TUNA_TOKEN=tt_private_value\n"
        "TUNA_LOCATION=ru\n"
        "TUNA_SUBDOMAIN=robopark\n"
        "TUNA_DOMAIN=\n"
        "TUNA_BIND=127.0.0.1:8080\n"
    )


def test_clean_install_requires_tuna_token_for_secure_browser_features():
    with pytest.raises(ValueError, match="invalid_tuna_token"):
        collect_tuna_configuration(
            getpass_fn=lambda _prompt: "",
            input_fn=lambda _prompt: "robopark.ru.tuna.am",
        )


def test_tuna_address_is_collected_and_split_into_subdomain_and_location():
    token = secrets.token_urlsafe(32)
    configuration = collect_tuna_configuration(
        getpass_fn=lambda _prompt: token,
        input_fn=lambda _prompt: "robopark.ru.tuna.am",
    )

    assert configuration.subdomain == "robopark"
    assert configuration.location == "ru"
    assert configuration.domain == ""
    assert configuration.public_origin == "https://robopark.ru.tuna.am"
    assert token not in repr(configuration)


def test_tuna_short_subdomain_uses_documented_ru_default():
    configuration = collect_tuna_configuration(
        getpass_fn=lambda _prompt: secrets.token_urlsafe(32),
        input_fn=lambda _prompt: "robopark",
    )

    assert configuration.public_origin == "https://robopark.ru.tuna.am"
    assert configuration.subdomain == "robopark"
    assert configuration.location == "ru"


def test_tuna_custom_domain_does_not_enable_subdomain_flag():
    configuration = collect_tuna_configuration(
        getpass_fn=lambda _prompt: secrets.token_urlsafe(32),
        input_fn=lambda _prompt: "robots.example.com",
    )

    assert configuration.domain == "robots.example.com"
    assert configuration.subdomain == ""
    assert configuration.public_origin == "https://robots.example.com"


@pytest.mark.parametrize("address", ["", "https://robopark.ru.tuna.am/path", "bad..example.com", "x\nTUNA_BIND=0.0.0.0:1"])
def test_tuna_address_rejects_missing_or_unsafe_values(address: str):
    with pytest.raises(ValueError, match="invalid_tuna_address"):
        collect_tuna_configuration(
            getpass_fn=lambda _prompt: secrets.token_urlsafe(32),
            input_fn=lambda _prompt: address,
        )


def test_owner_preset_skips_known_domain_and_username_prompts():
    credentials = collect_royal_credentials(
        username="tehblokdan", getpass_fn=lambda _: "StrongOwner-Password42",
        input_fn=lambda _: pytest.fail("username already preset"),
    )
    configuration = collect_tuna_configuration(
        address="robopark.ru.tuna.am", getpass_fn=lambda _: "test_tuna_token_not_real",
        input_fn=lambda _: pytest.fail("domain already preset"),
    )
    assert credentials.username == "tehblokdan"
    assert configuration.public_origin == "https://robopark.ru.tuna.am"
