from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from robopark_api.seed_from_file import SeedFileError, read_seed_file


def test_seed_file_is_private_strict_and_bounded(tmp_path: Path):
    path = tmp_path / "seed.json"
    path.write_text(
        json.dumps(
            {
                "username": "royal",
                "password": "Correct-Password-42!",
                "role": "royal",
            }
        )
    )
    os.chmod(path, 0o600)

    assert read_seed_file(path) == (
        "royal",
        "Correct-Password-42!",
        "royal",
    )

    os.chmod(path, 0o644)
    with pytest.raises(SeedFileError, match="unsafe_seed_file"):
        read_seed_file(path)


def test_seed_file_rejects_extra_fields_symlink_and_non_royal_role(tmp_path: Path):
    target = tmp_path / "target.json"
    target.write_text('{"username":"royal","password":"Strong-Password-42!","role":"admin"}')
    os.chmod(target, 0o600)
    link = tmp_path / "seed.json"
    link.symlink_to(target)

    with pytest.raises(SeedFileError, match="unsafe_seed_file"):
        read_seed_file(link)
    with pytest.raises(SeedFileError, match="invalid_seed_file"):
        read_seed_file(target)
