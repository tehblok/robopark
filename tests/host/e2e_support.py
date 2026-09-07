"""External adapters for the acceptance suite; no updater/doctor internals replaced.

InstallerScenarios supplies only its existing bootstrap setup and fake commands.
The dependency fixture is a filesystem replacement probe, not a buildable release:
real Docker dependency resolution remains an explicit target acceptance gate.
"""

import hashlib
import json
import multiprocessing
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from installer_scenarios import REPO, SECRETS, InstallerScenarios
from robopark_api.services.ops.archives import build_archive
from robopark_host import cli
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
        self.patch.setattr("robopark_host.updater.SystemRunner", lambda: self)
        self.patch.setattr(cli, "_system_runner", self.diagnostic_command)
        self.patch.setattr(cli, "_Http", lambda: self)
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
        self.raw = build_archive(
            kind="release",
            source_root=self.source,
            app_version="0.1.1",
            release_meta={
                "git_sha": "b" * 40,
                "migration_head": "next",
                "migration_compatibility": {"from_heads": ["initial"], "reversible": True},
            },
            signing_key=self.installer.key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        )
        self.github = self.make_github()
        self.patch.setattr("robopark_host.github_releases.GithubHttp", lambda: self.github)
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
        return cli.main(list(args))

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
        # Boot recovery executes the real updater/consumer dispatch before app/Tuna.
        self.command("update", "--recover")
        self.command("consume")
        self.run(["systemctl", "restart", "robopark.service"], timeout=900)
        assert self.wait_ready(
            project="robopark", config=self.paths.state / "current-compose.json", timeout=180
        )
        self.run(["systemctl", "start", "robopark-tuna.service"], timeout=90)

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
                code = self.command(*argv[3:])
                if code:
                    raise ReleaseError("command_failed")
            return b""
        if argv[0] == "curl":
            return (
                b'{"status":"degraded"}\n503'
                if self.fail in ("public_health", "tuna")
                else b'{"status":"ready"}\n200'
            )
        if argv[:2] == ["systemctl", "stop"] and self.check_multiworker:
            self.multiworker_probe()
        if argv[0] == "systemctl" and argv[-1] == "robopark-tuna.service" and self.fail == "tuna":
            raise ReleaseError("command_failed")
        if argv[:3] == ["docker", "image", "inspect"]:
            return ("sha256:" + ("3" if "api" in argv[-1] else "4") * 64).encode()
        if "--format" in argv and "config" in argv:
            return json.dumps(
                {
                    "services": {
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
        if self.fail == "build" and "build" in argv:
            raise ReleaseError("command_failed")
        if self.fail == "tests" and "/verify/scripts/verify.sh" in argv:
            raise ReleaseError("command_failed")
        if "upgrade" in argv:
            assert self.maintenance()
            (self.paths.var / "data/robopark.db").write_text("migrated")
            if self.fail == "migration":
                raise ReleaseError("command_failed")
        if any("SELECT version_num FROM alembic_version" in arg for arg in argv):
            return json.dumps(["next" if self.version() == "0.1.1" else "initial"]).encode()
        return b""

    def wait_ready(self, *, project, config, timeout):
        if project.startswith("robopark-candidate-"):
            isolated = json.loads(Path(config).read_text())
            assert str(self.paths.var / "data") not in json.dumps(isolated)
            return self.fail != "smoke"
        return self.fail != "both_health" and not (
            self.fail == "local_health" and self.version() == "0.1.1"
        )

    def get(self, url, *, timeout):
        if self.fail in ("tuna", "public_health") and url.startswith("https:"):
            raise OSError("tt_fixture_secret")
        return ReadyHttp().get(url, timeout=timeout)

    def diagnostic_command(self, command, *, timeout, max_output):
        argv = list(map(str, command))
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
                        for name in ("api", "web")
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
