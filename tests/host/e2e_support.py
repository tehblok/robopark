"""External adapters for the acceptance suite; no updater/doctor internals replaced.

InstallerScenarios supplies only its existing bootstrap setup and fake commands.
The dependency fixture is a filesystem replacement probe, not a buildable release:
real Docker dependency resolution remains an explicit target acceptance gate.
"""

import configparser
import hashlib
import importlib
import json
import multiprocessing
import os
import runpy
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from installer_scenarios import REPO, SECRETS, InstallerScenarios
from robopark_host.checks import CommandResult
from robopark_host.paths import HostPaths
from robopark_host.release import ReleaseError
from test_doctor import ReadyHttp
from test_github_releases import BASE, FakeGitHub


class PowerLoss(BaseException):
    pass


def database_writer(root, ops, database, result):
    os.environ["OPS_HOST_ROOT"] = root
    os.environ["OPS_DIR"] = ops
    os.environ["DATABASE_URL"] = "sqlite:///" + database
    from robopark_api.db import engine
    from robopark_api.services.ops.maintenance import HostMaintenanceActive
    from sqlalchemy import text

    try:
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO writer_probe VALUES (1)"))
        outcome = "write-escaped"
    except HostMaintenanceActive:
        outcome = "maintenance"
    Path(result).write_text(outcome)


class InstalledHost:
    def __init__(self, monkeypatch, *, interrupted=False):
        self.patch = monkeypatch
        self.fail = None
        self.launches = []
        self.calls = []
        self.app_active = True
        self.db_active = True
        self.tuna_active = True
        self.command_error = ReleaseError
        self.secrets = SECRETS
        self.check_multiworker = False
        self.writer_outcomes = []
        self.installer = InstallerScenarios()
        self.installer.setUp()
        self.installer.env["PATH"] = (
            str(REPO / "tests/host/fake-bin")
            + ":"
            + str(Path(sys.executable).parent)
            + ":"
            + os.environ["PATH"]
        )
        self.source = self.installer.source
        for name in (
            "apps/api/Dockerfile",
            "apps/api/pyproject.toml",
            "apps/api/uv.lock",
            "apps/web/Dockerfile",
            "apps/web/package.json",
            "apps/web/package-lock.json",
            "deploy/Dockerfile.api-tests",
            "scripts/verify.sh",
        ):
            target = self.source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / name, target)
        migration = self.source / "apps/api/alembic/versions/e2e.py"
        migration.parent.mkdir(parents=True)
        migration.write_text("revision = 'initial'\n")
        (self.source / "VERSION").write_text("0.1.0\n")
        self.installer.write_release("0.1.0")
        if interrupted:
            # Resume uses the identical authenticated payload after readiness failure.
            self.installer.run_installer(success=False, API_UNREADY="1")
            self.installer.run_installer("--resume")
        else:
            self.installer.run_installer()
        self.patch.setenv("ROBOPARK_TESTING", "1")
        self.patch.setenv("ROBOPARK_ROOT", str(self.installer.root))
        self.paths = HostPaths.from_root(self.installer.root)
        (self.paths.var / "data/robopark.db").write_text("original")
        (self.paths.var / "data/attachment").write_text("private-database-record")
        self.patch.setattr("platform.system", lambda: "Linux")
        self.patch.setattr("platform.machine", lambda: "aarch64")
        # TLS socket is external; a missing certificate yields a warning, not a failure.
        self.patch.setattr(
            "socket.create_connection", lambda *a, **kw: (_ for _ in ()).throw(OSError())
        )
        native_usage = shutil.disk_usage
        self.patch.setattr(
            shutil,
            "disk_usage",
            lambda p: SimpleNamespace(free=0) if self.fail == "disk" else native_usage(p),
        )
        # Advance only the updater's external monotonic clock for a simulated long outage.
        self.clock_offset = 0
        native_monotonic = time.monotonic
        self.patch.setattr(time, "monotonic", lambda: native_monotonic() + self.clock_offset)
        self.patch.setattr(
            time,
            "sleep",
            lambda seconds: setattr(self, "clock_offset", self.clock_offset + seconds),
        )
        self.changed_files = []
        for fixture, append in [("dependency-change", False), ("host-tools-change", True)]:
            changes = json.loads(
                (Path(__file__).parent / "fixtures/releases" / (fixture + ".json")).read_text()
            )
            for name, body in changes.items():
                target = self.source / name
                target.write_text((target.read_text() if append else "") + body)
                self.changed_files.append(name)
        (self.source / "VERSION").write_text("0.1.1\n")
        metadata = self.source / "deploy/release-metadata.json"
        metadata.write_text(
            json.dumps(
                {
                    "migration_head": "next",
                    "migration_compatibility": {"from_heads": ["initial"], "reversible": True},
                }
            )
        )
        (self.source / "apps/api/alembic/versions/initial.py").write_text(
            "revision = 'initial'\ndown_revision = None\n"
        )
        private = self.installer.base / "packer-private.pem"
        private.write_bytes(
            self.installer.key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        private.chmod(0o600)
        output = self.installer.base / "packed-release.zip"
        try:
            subprocess.run(
                [
                    sys.executable,
                    str(REPO / "scripts/release_pack.py"),
                    "--root",
                    str(self.source),
                    "--output",
                    str(output),
                    "--version",
                    "0.1.1",
                    "--git-sha",
                    "b" * 40,
                    "--metadata",
                    str(metadata),
                    "--signing-key",
                    str(private),
                ],
                check=True,
                capture_output=True,
            )
            self.raw = output.read_bytes()
        finally:
            private.unlink(missing_ok=True)
        self.github = self.make_github()
        (self.paths.etc / "updater.env").write_text(
            "GITHUB_REPOSITORY='team/robopark'\nGITHUB_TOKEN='github_fixture_secret'\nGITHUB_CHANNEL='stable'\nGITHUB_ENABLED='true'\n"
        )
        (self.paths.etc / "updater.env").chmod(0o600)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.installer.doCleanups()

    def version(self):
        return (self.paths.current / "VERSION").read_text().strip()

    def data(self):
        return (self.paths.var / "data/robopark.db").read_text()

    def maintenance(self):
        return (self.paths.ops / "public/maintenance.json").exists()

    def command(self, *args):
        return self.execute_entry((self.paths.opt / "host-tools/robopark").resolve(), args)

    def execute_entry(self, entry, args):
        """Load each child command from its actual host-tools source, like exec.

        Only the external runner/HTTP boundaries are injected. Swapping the package
        namespace prevents this process adapter from reusing old CLI/updater modules
        after self-update; nested worker and successor invocations restore their caller.
        """

        def host_module(name):
            return name == "robopark_host" or name.startswith("robopark_host.")

        previous_modules = {
            name: module for name, module in sys.modules.items() if host_module(name)
        }
        previous_path = sys.path[:]
        previous_argv = sys.argv[:]
        previous_bytecode = sys.dont_write_bytecode
        previous_error = self.command_error
        try:
            for name in previous_modules:
                del sys.modules[name]
            sys.path.insert(0, str(entry.parent))
            sys.argv = [str(entry), *args]
            sys.dont_write_bytecode = True
            successor_cli = importlib.import_module("robopark_host.cli")
            successor_updater = importlib.import_module("robopark_host.updater")
            successor_github = importlib.import_module("robopark_host.github_releases")
            self.command_error = importlib.import_module("robopark_host.release").ReleaseError
            successor_updater.SystemRunner = lambda: self
            successor_cli._system_runner = self.diagnostic_command
            successor_cli._Http = lambda: self
            successor_github.GithubHttp = lambda: self.github
            try:
                runpy.run_path(str(entry), run_name="__main__")
            except SystemExit as result:
                return result.code
            raise AssertionError("host entrypoint did not exit")
        finally:
            for name in list(sys.modules):
                if host_module(name):
                    del sys.modules[name]
            sys.modules.update(previous_modules)
            sys.path[:] = previous_path
            sys.argv[:] = previous_argv
            sys.dont_write_bytecode = previous_bytecode
            self.command_error = previous_error

    def installed_files(self):
        return {name: (self.paths.current / name).read_bytes() for name in self.changed_files}

    def result(self):
        return json.loads((self.paths.ops / "public/rebuild.result").read_text())

    def approve(self):
        raw = self.raw
        if self.fail == "tamper":
            import io
            import zipfile

            output = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(output, "w") as target:
                for name in source.namelist():
                    target.writestr(
                        name, b"x" * 64 if name == "manifest.sig" else source.read(name)
                    )
            raw = output.getvalue()
        (self.paths.ops / "artifacts/update-e2e.zip").write_bytes(raw)
        self.submit("update", artifact="update-e2e.zip")

    def submit(self, kind, **fields):
        command = {
            "kind": kind,
            "job_id": str(uuid4()),
            "actor_user_id": 1,
            "created_at": datetime.now(UTC).isoformat(),
            **fields,
        }
        (self.paths.ops / "inbox/approved.json").write_text(json.dumps(command))
        return self.command("consume")

    def reboot(self):
        # Execute installed unit entrypoints, without inventing a boot consumer.
        self.app_active = self.tuna_active = self.db_active = False
        with suppress(self.command_error):
            self.run(["systemctl", "start", "robopark.service"], timeout=3600)
        # After orders startup but does not require app success. The updater is
        # enabled independently and still runs after a failed ExecStartPre.
        unit = configparser.ConfigParser(interpolation=None)
        unit.read(self.paths.root / "etc/systemd/system/robopark-updater.service")
        self.command(*shlex.split(unit["Service"]["ExecStart"])[3:])
        if not self.wait_ready(
            project="robopark",
            config=self.paths.state / "current-compose.json",
            timeout=180,
        ):
            return False
        self.run(["systemctl", "start", "robopark-tuna.service"], timeout=90)
        return True

    def make_github(self):
        name = "robopark-release-0.1.1.zip"
        digest = hashlib.sha256(self.raw).hexdigest()
        assets = {
            201: self.raw,
            202: self.installer.key.sign(self.raw),
            203: f"{digest}  {name}\n".encode(),
            204: json.dumps(
                {
                    "format": 1,
                    "kind": "release",
                    "filename": name,
                    "size": len(self.raw),
                    "sha256": digest,
                    "app_version": "0.1.1",
                    "git_sha": "b" * 40,
                    "migration_head": "next",
                }
            ).encode(),
        }
        release = {
            "id": 101,
            "tag_name": "v0.1.1",
            "draft": False,
            "prerelease": False,
            "url": BASE + "/releases/101",
            "html_url": "https://github.com/team/robopark/releases/tag/v0.1.1",
            "body": "",
            "assets": [
                {
                    "id": 201 + i,
                    "name": name + suffix,
                    "size": len(assets[201 + i]),
                    "url": BASE + f"/releases/assets/{201 + i}",
                }
                for i, suffix in enumerate(("", ".sig", ".sha256", ".json"))
            ],
        }
        return FakeGitHub(release, assets)

    def discover(self, outage=False):
        self.github.failure = OSError("github_fixture_secret") if outage else None
        self.command("check-update")

    def available(self):
        return json.loads((self.paths.ops / "public/available-update.json").read_text())

    def run(self, argv, *, timeout, cwd=None, env=None, capture=False):
        argv = list(map(str, argv))
        self.calls.append(argv)
        if argv[0] == "python3":
            entry = Path(argv[2])
            if "--self-test" in argv:
                subprocess.run(
                    [sys.executable, "-B", str(entry), "--self-test"],
                    check=True,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                    capture_output=True,
                )
            else:
                self.launches.append(entry)
                code = self.execute_entry(entry, argv[3:])
                if code:
                    raise self.command_error("command_failed")
            return b""
        if argv[0] == "systemctl" and len(argv) > 2:
            action, unit = argv[1], argv[-1]
            if unit == "robopark.service":
                if action in ("stop", "restart"):
                    # Tuna Requires=app: explicit stop also stops the reverse dependent.
                    self.app_active = False
                    unit_config = configparser.ConfigParser(interpolation=None)
                    unit_config.read(self.paths.root / "etc/systemd/system/robopark-tuna.service")
                    if (
                        "robopark.service"
                        in unit_config.get("Unit", "Requires", fallback="").split()
                    ):
                        self.tuna_active = False
                if action in ("start", "restart"):
                    app_unit = configparser.ConfigParser(interpolation=None)
                    app_unit.read(self.paths.root / "etc/systemd/system/robopark.service")
                    command = shlex.split(app_unit["Service"]["ExecStartPre"])[3:]
                    if self.command(*command):
                        raise self.command_error("command_failed")
                    self.db_active = True
                    self.app_active = True
            elif unit == "robopark-tuna.service" and action in ("start", "restart", "try-restart"):
                if action != "try-restart" or self.tuna_active:
                    assert self.app_active, "Tuna started before application readiness"
                    self.tuna_active = self.fail != "tuna"
        if (
            argv[:2] == ["docker", "compose"]
            and "-p" in argv
            and argv[argv.index("-p") + 1] == "robopark"
        ):
            if "stop" in argv:
                self.app_active = False
            elif "up" in argv:
                self.db_active = True
                self.app_active = True
        if argv[0] == "curl":
            return (
                b'{"status":"degraded"}\n503'
                if self.fail in ("public_health", "tuna") or not self.tuna_active
                else b'{"status":"ready"}\n200'
            )
        if argv[:2] == ["systemctl", "stop"] and self.check_multiworker:
            self.multiworker_probe()
        if argv[0] == "systemctl" and argv[-1] == "robopark-tuna.service" and self.fail == "tuna":
            raise self.command_error("command_failed")
        if argv[:3] == ["docker", "image", "inspect"]:
            return ("sha256:" + ("3" if "api" in argv[-1] else "4") * 64).encode()
        if "--format" in argv and "config" in argv:
            return json.dumps(
                {
                    "services": {
                        "db": {
                            "image": "postgres:17.6-alpine",
                            "environment": {},
                            "volumes": [],
                        },
                        "api": {
                            "build": {"context": "../apps/api"},
                            "environment": {},
                            "volumes": [],
                        },
                        "web": {
                            "build": {"context": "../apps/web"},
                            "ports": [{"host_ip": "127.0.0.1", "published": "8080", "target": 80}],
                        },
                    }
                }
            ).encode()
        if "pg_dump" in argv:
            output = next(arg for arg in argv if arg.startswith("--file="))
            relative = output.removeprefix("--file=/host-rollbacks/")
            target = self.paths.ops / "rollbacks" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"PGDMP fixture")
        if "pg_restore" in argv and "--list" not in argv:
            assert self.db_active, "pg_restore ran before PostgreSQL was healthy"
        if self.fail == "build" and "build" in argv:
            raise self.command_error("command_failed")
        if "upgrade" in argv:
            assert self.maintenance()
            (self.paths.var / "data/robopark.db").write_text("migrated")
            if self.fail == "migration":
                raise self.command_error("command_failed")
        if any("SELECT version_num FROM alembic_version" in arg for arg in argv):
            if "psql" in argv:
                return ("next" if self.version() == "0.1.1" else "initial").encode()
            return json.dumps(["next" if self.version() == "0.1.1" else "initial"]).encode()
        return b""

    def wait_ready(self, *, project, config, timeout):
        if project.startswith("robopark-candidate-"):
            isolated = json.loads(Path(config).read_text())
            assert str(self.paths.var / "data") not in json.dumps(isolated)
            return self.fail != "smoke"
        if self.fail == "restore_health":
            with sqlite3.connect(self.paths.var / "data/robopark.db") as connection:
                if connection.execute("SELECT value FROM probe").fetchone()[0] == "snapshot":
                    return False
        return (
            self.app_active
            and self.fail != "both_health"
            and not (self.fail == "local_health" and self.version() == "0.1.1")
        )

    def get(self, url, *, timeout):
        if (self.fail in ("tuna", "public_health") or not self.tuna_active) and url.startswith(
            "https:"
        ):
            raise OSError("tt_fixture_secret")
        return ReadyHttp().get(url, timeout=timeout)

    def diagnostic_command(self, command, *, timeout, max_output):
        argv = list(map(str, command))
        if argv[:2] == ["systemctl", "is-active"] and argv[-1] == "robopark-tuna.service":
            return CommandResult(returncode=0 if self.tuna_active else 3)
        if argv[0] == "systemctl" and argv[1] in ("start", "restart", "daemon-reload"):
            try:
                self.run(argv, timeout=timeout)
                return CommandResult()
            except Exception:
                return CommandResult(returncode=1)
        if argv[0] == "journalctl":
            return CommandResult(
                stdout=json.dumps(
                    {
                        "MESSAGE": " ".join(SECRETS),
                        "_CMDLINE": "TUNA_TOKEN=tt_fixture_secret",
                        "PRIORITY": "6",
                    }
                )
            )
        if "ps" in argv:
            return CommandResult(
                stdout=json.dumps(
                    [
                        {"Service": name, "State": "running", "Health": "healthy", "ExitCode": 0}
                        for name in ("db", "api", "web")
                    ]
                )
            )
        if "alembic" in argv:
            return CommandResult(
                stdout=("next" if self.version() == "0.1.1" else "initial") + " (head)"
            )
        if argv[0] == "timedatectl":
            return CommandResult(stdout="yes\n")
        if argv[0] == "df":
            return CommandResult(
                stdout="Filesystem Blocks Used Available Capacity Mounted\nfake 100 20 80 20% /\n"
            )
        if argv[0] == "free":
            return CommandResult(stdout="Mem: 8192 2048 512 0 0 6000\nSwap: 2048 0 2048\n")
        if argv[0] == "uptime":
            return CommandResult(stdout="load average: 0.1, 0.1, 0.1")
        if argv[0] == "du":
            return CommandResult(stdout="10\tpath\n")
        if argv[0] == "cat":
            return CommandResult(stdout="45000")
        return CommandResult()

    def assert_boundaries(self):
        config = json.loads((self.paths.state / "current-compose.json").read_text())
        mounts = config["services"]["api"]["volumes"]
        for mount in mounts:
            source = Path(mount["source"])
            assert source not in (self.paths.ops, self.paths.etc, self.paths.releases)
            assert "docker.sock" not in str(source)
            assert source != self.paths.state
        assert next(m for m in mounts if m["target"] == "/host-ops/public")["read_only"]
        api = config["services"]["api"]
        assert api["environment"]["OPS_HOST_ROOT"] == "/host-ops"
        key = api["environment"]["OPS_RELEASE_PUBLIC_KEY_PATH"]
        assert next(m for m in mounts if m["target"] == key)["read_only"]
        assert self.paths.state.joinpath("current-compose.json").is_symlink()
        for service in config["services"].values():
            assert service["image"].startswith("sha256:")
            assert "build" not in service
        assert self.paths.state.stat().st_mode & 0o777 == 0o700
        assert (self.paths.state / "install.json").stat().st_mode & 0o777 == 0o600
        assert (self.paths.etc / "host.env").stat().st_mode & 0o777 == 0o600

    def multiworker_probe(self):
        # Simulate the API's three bind mounts: root state is absent from its view.
        view = self.installer.base / "api-view"
        for name in ("inbox", "artifacts", "public"):
            (view / name).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(
            self.paths.ops / "public/maintenance.json", view / "public/maintenance.json"
        )
        database = self.installer.base / "shared-writer-probe.db"
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE writer_probe (id integer)")
        context = multiprocessing.get_context("spawn")
        workers = []
        for i in range(4):
            output = self.installer.base / f"writer-{i}.txt"
            process = context.Process(
                target=database_writer,
                args=(str(view), str(self.paths.var / "api-ops"), str(database), str(output)),
            )
            process.start()
            workers.append((process, output))
        for process, output in workers:
            process.join(timeout=20)
            assert process.exitcode == 0
            self.writer_outcomes.append(output.read_text())
        with sqlite3.connect(database) as db:
            assert db.execute("SELECT count(*) FROM writer_probe").fetchone() == (0,)
