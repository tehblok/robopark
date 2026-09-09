"""Offline regressions for publisher alignment, hard HTTP bounds and private state."""

import io
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from test_github_releases import config
from test_github_releases import github as github

ROOT = Path(__file__).resolve().parents[2]


def publish_flags(tmp_path, version, *, tag=None):
    """Execute the actual workflow publisher with only the external gh boundary replaced."""
    workspace = tmp_path / "publisher"
    workspace.mkdir(exist_ok=True)
    (workspace / "VERSION").write_text(version)
    capture = workspace / "gh-arguments.json"
    gh = workspace / "gh"
    gh.write_text(
        f"#!{sys.executable}\nimport json, sys\nfrom pathlib import Path\nPath({str(capture)!r}).write_text(json.dumps(sys.argv[1:]))\n"
    )
    gh.chmod(0o755)
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    script = next(
        step["run"]
        for step in workflow["jobs"]["release"]["steps"]
        if step.get("name") == "Publish verified assets"
    )
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=workspace,
        env={
            **os.environ,
            "PATH": str(workspace) + os.pathsep + os.environ["PATH"],
            "RELEASE_TAG": tag or "v" + version,
        },
        capture_output=True,
        timeout=5,
    )
    return result, json.loads(capture.read_text()) if capture.exists() else None


@pytest.mark.parametrize("version,prerelease", [("1.3.0", False), ("1.3.0-rc.1", True)])
def test_validated_tag_publishes_matching_channel(tmp_path, version, prerelease):
    source = tmp_path / "version-sources"
    files = {
        "VERSION": version,
        "apps/api/pyproject.toml": f'[project]\nversion="{version}"\n',
        "apps/api/uv.lock": (
            'version = 1\n\n[[package]]\nname = "robopark-api"\n'
            f'version = "{version}"\nsource = {{ editable = "." }}\n'
        ),
        "apps/web/package.json": json.dumps({"version": version}),
        "apps/web/package-lock.json": json.dumps(
            {"version": version, "packages": {"": {"version": version}}}
        ),
        "apps/api/src/robopark_api/services/ops/context.py": f'APP_VERSION = "{version}"\n',
    }
    for name, content in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    check = runpy.run_path(str(ROOT / "scripts/check-release-version.py"))["check"]
    assert check(source, "v" + version) == version
    result, args = publish_flags(tmp_path, version)
    assert result.returncode == 0, result.stderr.decode()
    assert args[:3] == ["release", "create", "v" + version]
    assert ("--prerelease" in args) is prerelease
    assert "--verify-tag" in args
    assert f"artifacts/robopark-release-{version}.zip.sig" in args


def test_version_gate_rejects_stale_api_lock(tmp_path):
    version = "1.3.0"
    source = tmp_path / "version-sources"
    files = {
        "VERSION": version,
        "apps/api/pyproject.toml": f'[project]\nversion="{version}"\n',
        "apps/api/uv.lock": (
            'version = 1\n\n[[package]]\nname = "robopark-api"\n'
            'version = "1.2.9"\nsource = { editable = "." }\n'
        ),
        "apps/web/package.json": json.dumps({"version": version}),
        "apps/web/package-lock.json": json.dumps(
            {"version": version, "packages": {"": {"version": version}}}
        ),
        "apps/api/src/robopark_api/services/ops/context.py": f'APP_VERSION = "{version}"\n',
    }
    for name, content in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    check = runpy.run_path(str(ROOT / "scripts/check-release-version.py"))["check"]
    with pytest.raises(ValueError):
        check(source, "v" + version)


def test_publisher_refuses_mismatched_tag(tmp_path):
    result, args = publish_flags(tmp_path, "1.3.0-rc.1", tag="v1.3.0")
    assert result.returncode != 0
    assert args is None


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        1,
        "value",
        {"release": None, "consumed": False},
        {"release": {}, "consumed": False},
        {"release": {}, "consumed": "false"},
    ],
)
def test_bad_private_history_invalidates_public_availability(host_paths, github, bad):
    from robopark_host.github_releases import check_latest_release

    assert check_latest_release(config(host_paths), github) is not None
    history = next(host_paths.state.glob("github-*.json"))
    history.write_text(json.dumps(bad))
    assert check_latest_release(config(host_paths), github) is None
    public = json.loads((host_paths.ops / "public/available-update.json").read_text())
    assert public["state"] == "discovery_stale" and public["release"] is None


def test_late_eof_is_rejected_even_when_it_completes(monkeypatch):
    from robopark_host.github_releases import GithubHttp
    from robopark_host.release import ReleaseError

    clock = [0]
    monkeypatch.setattr("robopark_host.github_releases.time.monotonic", lambda: clock[0])

    class Response(io.BytesIO):
        status = 200
        headers = {}

        def read1(self, size):
            clock[0] = 121
            return b""

    class Opener:
        def open(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr("urllib.request.build_opener", lambda *a: Opener())
    with pytest.raises(ReleaseError):
        list(
            GithubHttp().iter_bytes(
                "https://api.github.com/repos/team/robopark/releases",
                token="",
                asset=False,
                limit=1024,
            )
        )


@pytest.mark.parametrize("stage", ["open", "headers", "redirect", "chunk_framing", "eof"])
def test_transport_deadline_interrupts_blocking_calls_and_releases_lock(
    host_paths, github, monkeypatch, tmp_path, stage
):
    import fcntl
    import multiprocessing
    import time

    from robopark_host.github_releases import GithubHttp, check_latest_release

    reached = tmp_path / "blocked-stage"
    before = {child.pid for child in multiprocessing.active_children()}

    def drip():
        reached.write_text(stage)
        # Every byte arrives before a socket's inactivity timeout. The complete
        # header/chunk line would otherwise take much longer than the deadline.
        for _ in range(40):
            time.sleep(0.01)
        return b"0\r\n"

    from http.client import HTTPResponse

    class DripStream(io.BytesIO):
        def readline(self, size=-1):
            if (stage == "headers" and self.tell() == len(b"HTTP/1.1 200 OK\r\n")) or (
                stage == "chunk_framing"
                and self.tell() == len(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n")
            ):
                drip()
            return super().readline(size)

    class Socket:
        def makefile(self, mode):
            return DripStream(
                b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n[]\r\n0\r\n\r\n"
            )

    class Response(io.BytesIO):
        status = 200
        headers = {}

        def read1(self, size):
            drip()
            return b""

    class Opener:
        def open(self, *args, **kwargs):
            if stage in {"headers", "chunk_framing"}:
                response = HTTPResponse(Socket())
                response.begin()
                return response
            if stage in {"open", "redirect"}:
                drip()
            return Response()

    monkeypatch.setattr("urllib.request.build_opener", lambda *a: Opener())
    http = GithubHttp()
    http.deadline = time.monotonic() + 0.10
    started = time.monotonic()
    assert check_latest_release(config(host_paths), http) is None
    elapsed = time.monotonic() - started
    assert elapsed < 0.30, f"transport held host.lock for {elapsed:.2f}s"
    assert reached.read_text() == stage
    assert (
        json.loads((host_paths.ops / "public/available-update.json").read_text())["state"]
        == "discovery_stale"
    )
    assert {child.pid for child in multiprocessing.active_children()} == before
    with (host_paths.ops / "host.lock").open("rb") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def test_whole_client_budget_is_shared_across_requests(monkeypatch):
    import time

    from robopark_host.github_releases import GithubHttp
    from robopark_host.release import ReleaseError

    class Response(io.BytesIO):
        status = 200
        headers = {}

    class Opener:
        def open(self, *args, **kwargs):
            time.sleep(0.08)
            return Response(b"[]")

    monkeypatch.setattr("urllib.request.build_opener", lambda *a: Opener())
    http = GithubHttp()
    http.deadline = time.monotonic() + 0.14
    url = "https://api.github.com/repos/team/robopark/releases"
    assert b"".join(http.iter_bytes(url, token="", asset=False, limit=2)) == b"[]"
    with pytest.raises(ReleaseError):
        list(http.iter_bytes(url, token="", asset=False, limit=2))


def test_download_timeout_removes_partial_and_preserves_current(host_paths, github, monkeypatch):
    import time

    from robopark_host.github_releases import (
        GithubHttp,
        check_latest_release,
        download_approved_release,
    )
    from robopark_host.release import ReleaseError

    release = check_latest_release(config(host_paths), github)
    reached = host_paths.root / "zip-read-started"

    class Response(io.BytesIO):
        status = 200
        headers = {}

        def read1(self, size):
            if self.getvalue() == github.assets[201]:
                reached.write_text("reading")
                time.sleep(0.4)
            return super().read1(size)

    class Opener:
        def open(self, request, **kwargs):
            if "/assets/" in request.full_url:
                body = github.assets[int(request.full_url.rsplit("/", 1)[1])]
            else:
                body = json.dumps(github.release).encode()
            return Response(body)

    monkeypatch.setattr("urllib.request.build_opener", lambda *a: Opener())
    http = GithubHttp()
    http.deadline = time.monotonic() + 0.15
    with pytest.raises(ReleaseError):
        download_approved_release(release, host_paths, http)
    assert reached.exists()
    assert not list((host_paths.state / "github-artifacts").glob("*.partial"))
    assert not list((host_paths.state / "github-artifacts").glob("*.zip"))
    assert (host_paths.current / "VERSION").read_text() == "1.2.0\n"
    assert (
        json.loads((host_paths.ops / "public/available-update.json").read_text())["state"]
        == "discovery_stale"
    )


def test_deadline_kills_transport_that_ignores_termination(monkeypatch):
    import multiprocessing
    import signal
    import time

    from robopark_host.github_releases import GithubHttp
    from robopark_host.release import ReleaseError

    before = {child.pid for child in multiprocessing.active_children()}
    previous = signal.getsignal(signal.SIGTERM)

    class Opener:
        def open(self, *args, **kwargs):
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            time.sleep(0.8)
            raise OSError("LEAK")

    monkeypatch.setattr("urllib.request.build_opener", lambda *a: Opener())
    client = GithubHttp()
    client.deadline = time.monotonic() + 0.1
    started = time.monotonic()
    try:
        with pytest.raises(ReleaseError):
            list(
                client.iter_bytes(
                    "https://api.github.com/repos/team/robopark/releases",
                    token="",
                    asset=False,
                    limit=1024,
                )
            )
    finally:
        signal.signal(signal.SIGTERM, previous)
    assert time.monotonic() - started < 0.4
    assert {child.pid for child in multiprocessing.active_children()} == before


@pytest.mark.parametrize(
    "field,value",
    [("release_id", None), ("version", []), ("assets", []), ("size", True), ("repository", None)],
)
def test_bad_history_release_field_is_stale(host_paths, github, field, value):
    from robopark_host.github_releases import check_latest_release

    assert check_latest_release(config(host_paths), github) is not None
    history = next(host_paths.state.glob("github-*.json"))
    record = json.loads(history.read_text())
    record["release"][field] = value
    history.write_text(json.dumps(record))
    assert check_latest_release(config(host_paths), github) is None
    assert (
        json.loads((host_paths.ops / "public/available-update.json").read_text())["state"]
        == "discovery_stale"
    )
