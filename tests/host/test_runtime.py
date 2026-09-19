"""PostgreSQL production runtime and adaptive host sizing contracts."""

from pathlib import Path

import yaml

from robopark_host.runtime import DatabaseProfile, HostProfile, select_host_profile


def test_production_compose_uses_local_postgresql_17_and_health_gates_api():
    compose = yaml.safe_load(Path("deploy/docker-compose.yml").read_text())

    database = compose["services"]["db"]
    api = compose["services"]["api"]

    assert database["image"].startswith("postgres:17")
    assert database["ports"] == ["127.0.0.1:5432:5432"]
    assert database["healthcheck"]["test"][:2] == ["CMD-SHELL", "pg_isready -U robopark -d robopark"]
    assert database["volumes"] == ["${ROBOPARK_POSTGRES_SOURCE:-robopark_postgres}:/var/lib/postgresql/data"]
    assert api["depends_on"]["db"]["condition"] == "service_healthy"
    assert api["environment"]["DATABASE_URL"].startswith("postgresql+")
    assert "sqlite" not in Path("deploy/docker-compose.yml").read_text().lower()


def test_vim4_profile_keeps_postgres_and_api_bounded():
    profile = select_host_profile(memory_kib=8 * 1024 * 1024, cpu_count=4)

    assert profile == HostProfile(
        name="vim4-safe",
        api_workers=2,
        api_memory="2g",
        postgres_memory="1536m",
        postgres_shared_buffers="512MB",
        postgres_max_connections=40,
    )


def test_orin_profile_scales_only_after_ram_and_cpu_are_available():
    profile = select_host_profile(memory_kib=32 * 1024 * 1024, cpu_count=12)

    assert profile.name == "orin"
    assert profile.api_workers == 4
    assert profile.postgres_memory == "6g"
    assert profile.postgres_shared_buffers == "2GB"
    assert profile.postgres_max_connections == 100


def test_database_profile_never_places_password_in_command_arguments():
    profile = DatabaseProfile.from_environment(
        {
            "POSTGRES_USER": "robopark",
            "POSTGRES_DB": "robopark",
            "POSTGRES_PASSWORD_FILE": "/run/secrets/postgres-password",
        }
    )

    assert profile.dsn == "postgresql+psycopg://robopark@db:5432/robopark"
    assert profile.dump_command(Path("/backup/robopark.dump")) == [
        "pg_dump",
        "--format=custom",
        "--file=/backup/robopark.dump",
        "--dbname=postgresql://robopark@db:5432/robopark",
    ]
    assert profile.health_command == ["pg_isready", "-U", "robopark", "-d", "robopark"]


def test_systemd_keeps_database_running_while_application_writers_are_stopped():
    unit = Path("deploy/systemd/robopark.service").read_text()

    start = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
    stop = next(line for line in unit.splitlines() if line.startswith("ExecStop="))
    assert start.endswith("db api web")
    assert stop.endswith("web api")
    assert " db" not in stop
