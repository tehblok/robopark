from __future__ import annotations

import json
import os
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
    configuration = collect_tuna_configuration(getpass_fn=lambda _prompt: token)

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
        collect_tuna_configuration(getpass_fn=lambda _prompt: "")
