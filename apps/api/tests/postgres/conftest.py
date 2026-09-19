from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Iterator
from uuid import uuid4

import pytest


@pytest.fixture(scope="session")
def postgres_database_url() -> Iterator[str]:
    if os.environ.get("ROBOPARK_POSTGRES_TESTS") != "1":
        pytest.skip("run through ./scripts/verify.sh api-postgres")

    name = f"robopark-postgres-test-{uuid4().hex[:12]}"
    subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            name,
            "--env",
            "POSTGRES_DB=robopark",
            "--env",
            "POSTGRES_USER=robopark",
            "--env",
            "POSTGRES_PASSWORD=robopark-test",
            "--publish",
            "127.0.0.1::5432",
            "postgres:17-alpine",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        for _attempt in range(60):
            ready = subprocess.run(
                ["docker", "exec", name, "pg_isready", "-U", "robopark", "-d", "robopark"],
                check=False,
                capture_output=True,
                text=True,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.25)
        else:
            logs = subprocess.run(
                ["docker", "logs", name], check=False, capture_output=True, text=True
            )
            pytest.fail(f"PostgreSQL did not become ready:\n{logs.stdout}\n{logs.stderr}")

        port = (
            subprocess.run(
                ["docker", "port", name, "5432/tcp"],
                check=True,
                capture_output=True,
                text=True,
            )
            .stdout.strip()
            .rsplit(":", 1)[-1]
        )
        yield f"postgresql+psycopg://robopark:robopark-test@127.0.0.1:{port}/robopark"
    finally:
        subprocess.run(["docker", "rm", "--force", name], check=False, capture_output=True)
