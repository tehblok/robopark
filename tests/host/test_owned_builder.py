"""An owned Buildx builder is the only target for Robopark builds and pruning."""

import json
from types import SimpleNamespace

import pytest
from robopark_host.owned_builder import ensure_owned_builder, owned_builder_name


def test_bootstrap_commands_share_the_host_runner_docker_config(monkeypatch, tmp_path):
    from robopark_host import runtime

    monkeypatch.setenv("ROBOPARK_TESTING", "1")
    monkeypatch.setenv("ROBOPARK_ROOT", str(tmp_path))
    monkeypatch.setenv("DOCKER_CONTEXT", "remote-production")
    monkeypatch.setenv("DOCKER_HOST", "tcp://remote.invalid:2375")
    seen = []

    def run(command, **kwargs):
        environment = kwargs["env"]
        seen.append((list(command), environment["DOCKER_CONFIG"], environment.get("DOCKER_HOST"), environment.get("DOCKER_CONTEXT")))
        return SimpleNamespace(stdout="")

    def stream(command, log_path, *, env):
        del log_path
        seen.append((list(command), env["DOCKER_CONFIG"], env.get("DOCKER_HOST"), env.get("DOCKER_CONTEXT")))
        return ""

    monkeypatch.setattr(runtime.subprocess, "run", run)
    monkeypatch.setattr(runtime, "_stream_build", stream)
    runtime._run(["docker", "buildx", "inspect", "example"])
    runtime._run(["docker", "compose", "build", "api"])

    expected = str(tmp_path / "var/lib/robopark/ops/docker-config")
    assert seen == [
        (["docker", "buildx", "inspect", "example"], expected, "unix:///var/run/docker.sock", None),
        (["docker", "compose", "build", "api"], expected, "unix:///var/run/docker.sock", None),
    ]


class Buildx:
    def __init__(self, *, driver="docker-container", owner=True):
        self.driver = driver
        self.owner = owner
        self.created = False
        self.commands = []

    def run(self, argv, **kwargs):
        self.commands.append((argv, kwargs))
        if argv[:3] == ["docker", "buildx", "create"]:
            self.created = True
            return b""
        if argv[:3] == ["docker", "buildx", "inspect"]:
            if not self.created:
                raise FileNotFoundError("builder absent")
            return f"Name: {argv[-1]}\nDriver: {self.driver}\n".encode()
        if argv[:3] == ["docker", "inspect", "--type"]:
            name = argv[-1].removeprefix("buildx_buildkit_").removesuffix("0")
            return json.dumps([f"ROBOPARK_BUILDER_OWNER={name if self.owner else 'foreign'}"]).encode()
        raise AssertionError(argv)


@pytest.mark.parametrize("fatal_step", ["create", "build"])
def test_missing_builder_probe_does_not_hide_fatal_ota_log(host_paths, fatal_step):
    import sys

    from robopark_host.release import ReleaseError
    from robopark_host.updater import SystemRunner

    log = host_paths.root / "var/log/robopark/ota-update.log"

    class ProcessFixture(SystemRunner):
        def run(self, argv, **kwargs):
            if argv[:3] == ["docker", "buildx", "inspect"]:
                if "--bootstrap" in argv:
                    message, status = f"Name: {argv[-1]}\nDriver: docker-container", 0
                else:
                    message, status = "expected builder absent", 1
            elif argv[:3] == ["docker", "buildx", "create"]:
                message, status = "fatal create failure", int(fatal_step == "create")
            elif argv[:3] == ["docker", "inspect", "--type"]:
                name = argv[-1].removeprefix("buildx_buildkit_").removesuffix("0")
                message, status = json.dumps([f"ROBOPARK_BUILDER_OWNER={name}"]), 0
            else:
                assert argv == ["build-web"]
                message, status = "fatal npm build failure", 1
            return super().run(
                [sys.executable, "-c", "import sys; print(sys.argv[1]); sys.exit(int(sys.argv[2]))", message, str(status)],
                **kwargs,
            )

    runner = ProcessFixture(failure_log=log)
    if fatal_step == "create":
        with pytest.raises(ReleaseError):
            ensure_owned_builder(host_paths, runner)
        assert log.read_text() == "fatal create failure\n"
    else:
        ensure_owned_builder(host_paths, runner)
        assert not log.exists(), "handled absence must not occupy the fatal-error log"
        with pytest.raises(ReleaseError):
            runner.run(["build-web"], timeout=5)
        assert log.read_text() == "fatal npm build failure\n"
    assert log.stat().st_mode & 0o777 == 0o600


def test_builder_identity_is_durable_and_reused_after_interrupted_creation(host_paths):
    class Interrupted(Buildx):
        def run(self, argv, **kwargs):
            if argv[:3] == ["docker", "buildx", "create"]:
                raise OSError("interrupted")
            return super().run(argv, **kwargs)

    with pytest.raises(OSError):
        ensure_owned_builder(host_paths, Interrupted())
    name = owned_builder_name(host_paths)
    assert name is not None and name.startswith("robopark-buildkit-")

    buildx = Buildx()
    assert ensure_owned_builder(host_paths, buildx) == name
    assert ensure_owned_builder(host_paths, buildx) == name
    create = [argv for argv, _ in buildx.commands if argv[:3] == ["docker", "buildx", "create"]]
    assert create == [
        ["docker", "buildx", "create", "--name", name, "--driver", "docker-container",
         "--driver-opt", "default-load=true", "--driver-opt", f"env.ROBOPARK_BUILDER_OWNER={name}"]
    ]


def test_builder_identity_rejects_foreign_driver_before_any_build(host_paths):
    buildx = Buildx(driver="docker")
    with pytest.raises(ValueError, match="builder_identity_invalid"):
        ensure_owned_builder(host_paths, buildx)
    assert buildx.created


def test_builder_with_matching_name_but_foreign_container_owner_is_rejected(host_paths):
    from robopark_host.state import atomic_write_json

    name = "robopark-buildkit-" + "d" * 32
    atomic_write_json(host_paths.state / "buildkit-builder.json", {
        "schema": 1, "name": name, "driver": "docker-container",
    })
    buildx = Buildx(owner=False)
    buildx.created = True
    with pytest.raises(ValueError, match="builder_identity_invalid"):
        ensure_owned_builder(host_paths, buildx)
    assert not any(argv[:3] == ["docker", "buildx", "create"] for argv, _ in buildx.commands)


@pytest.mark.parametrize("damage", ["symlink", "mode", "name", "driver"])
def test_untrusted_builder_receipt_never_authorizes_docker(host_paths, damage):
    path = host_paths.state / "buildkit-builder.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {
        "schema": 1,
        "name": "robopark-buildkit-" + "c" * 32,
        "driver": "docker-container",
    }
    if damage == "name":
        value["name"] = "default"
    if damage == "driver":
        value["driver"] = "docker"
    target = path
    if damage == "symlink":
        target = host_paths.state / "foreign.json"
    target.write_text(json.dumps(value))
    target.chmod(0o600)
    if damage == "symlink":
        path.symlink_to(target)
    if damage == "mode":
        path.chmod(0o666)
    buildx = Buildx()
    with pytest.raises(ValueError, match="builder_ownership_invalid"):
        ensure_owned_builder(host_paths, buildx)
    assert buildx.commands == []
