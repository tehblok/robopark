from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from robopark_ota.credentials import (
    RoyalCredentials,
    collect_royal_credentials,
    credential_file,
    seed_command,
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
