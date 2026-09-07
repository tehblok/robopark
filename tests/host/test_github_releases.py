"""Offline GitHub trust boundary, real signing and filesystem publication."""

import hashlib
import io
import json
from datetime import UTC, datetime
from urllib.request import Request
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from robopark_api.services.ops.archives import build_archive

BASE = "https://api.github.com/repos/team/robopark"


class FakeGitHub:
    def __init__(self, release, assets):
        self.release = release
        self.assets = assets
        self.failure = None
        self.calls = []

    def iter_bytes(self, url, *, token, asset, limit):
        self.calls.append(url)
        if self.failure:
            raise self.failure
        assert url.startswith(BASE + "/releases"), "untrusted API URL used"
        if asset:
            raw = self.assets[int(url.rsplit("/", 1)[1])]
        elif url == BASE + "/releases?per_page=100":
            raw = json.dumps([self.release]).encode()
        else:
            assert url == BASE + "/releases/101"
            raw = json.dumps(self.release).encode()
        for start in range(0, len(raw), 128):
            yield raw[start : start + 128]


@pytest.fixture
def github(host_paths, tmp_path):
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    source = tmp_path / "source"
    source.mkdir()
    (source / "VERSION").write_text("1.3.0\n")
    raw = build_archive(
        kind="release",
        source_root=source,
        app_version="1.3.0",
        release_meta={"git_sha": "a" * 40, "migration_head": "0017"},
        signing_key=private,
    )
    name = "robopark-release-1.3.0.zip"
    digest = hashlib.sha256(raw).hexdigest()
    meta = {
        "format": 1,
        "kind": "release",
        "filename": name,
        "size": len(raw),
        "sha256": digest,
        "app_version": "1.3.0",
        "git_sha": "a" * 40,
        "migration_head": "0017",
    }
    assets = {
        201: raw,
        202: key.sign(raw),
        203: f"{digest}  {name}\n".encode(),
        204: json.dumps(meta).encode(),
    }
    release = {
        "id": 101,
        "tag_name": "v1.3.0",
        "draft": False,
        "prerelease": False,
        "url": BASE + "/releases/101",
        "html_url": "https://github.com/team/robopark/releases/tag/v1.3.0",
        "body": "<script>LEAK</script>",
        "assets": [
            {
                "id": 201 + i,
                "name": name + suffix,
                "size": len(assets[201 + i]),
                "url": BASE + f"/releases/assets/{201 + i}",
                "browser_download_url": "https://evil.invalid/?token=LEAK",
            }
            for i, suffix in enumerate(("", ".sig", ".sha256", ".json"))
        ],
    }
    host_paths.etc.mkdir(parents=True)
    (host_paths.etc / "release-public-key.pem").write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    host_paths.current.mkdir(parents=True)
    (host_paths.current / "VERSION").write_text("1.2.0\n")
    env = host_paths.etc / "updater.env"
    env.write_text(
        "GITHUB_REPOSITORY=team/robopark\nGITHUB_TOKEN=github_pat_LEAK\nGITHUB_CHANNEL=stable\nGITHUB_ENABLED=true\n"
    )
    env.chmod(0o600)
    return FakeGitHub(release, assets)


def config(paths):
    from robopark_host.github_releases import GithubConfig

    return GithubConfig.from_paths(paths)


def test_private_discovery_is_allowlisted_without_installing(host_paths, github):
    from robopark_host.github_releases import check_latest_release

    release = check_latest_release(config(host_paths), github)
    assert release.version == "1.3.0"
    assert release.release_id == 101
    private = host_paths.state / "available-update.json"
    public = host_paths.ops / "public/available-update.json"
    assert private.stat().st_mode & 0o777 == 0o600
    assert public.stat().st_mode & 0o777 == 0o644
    for path in (private, public):
        assert "LEAK" not in path.read_text()
        assert "https:" not in path.read_text()
    assert json.loads(public.read_text())["state"] == "available"
    assert not (host_paths.ops / "artifacts").exists()
    assert github.calls == [
        BASE + "/releases?per_page=100",
        BASE + "/releases/assets/204",
        BASE + "/releases/assets/203",
    ]


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "draft",
        "foreign",
        "foreign_asset",
        "tag",
        "old",
        "metadata",
        "digest",
        "size",
        "duplicate",
    ],
)
def test_discovery_rejects_untrusted_or_incomplete_release(host_paths, github, mutation):
    from robopark_host.github_releases import check_latest_release

    if mutation == "missing":
        github.release["assets"].pop()
    if mutation == "draft":
        github.release["draft"] = True
    if mutation == "foreign":
        github.release["url"] = "https://api.github.com/repos/evil/repo/releases/101"
    if mutation == "foreign_asset":
        github.release["assets"][0]["url"] = "https://evil.invalid"
    if mutation == "tag":
        github.release["tag_name"] = "v01.3.0"
    if mutation == "old":
        (host_paths.current / "VERSION").write_text("1.3.0")
    if mutation == "metadata":
        github.assets[204] = github.assets[204].replace(b"1.3.0", b"1.4.0")
    if mutation == "digest":
        github.assets[203] = b"0" * len(github.assets[203])
    if mutation == "size":
        github.release["assets"][0]["size"] += 1
    if mutation == "duplicate":
        github.release["assets"].append(github.release["assets"][0])
    assert check_latest_release(config(host_paths), github) is None
    assert not (host_paths.ops / "artifacts").exists()


def test_network_failure_invalidates_approval_and_preserves_current(host_paths, github):
    from robopark_host.github_releases import (
        check_latest_release,
        download_approved_release,
    )
    from robopark_host.release import ReleaseError

    release = check_latest_release(config(host_paths), github)
    github.failure = OSError("Authorization: Bearer LEAK https://signed/?token=LEAK")
    assert check_latest_release(config(host_paths), github) is None
    state = (host_paths.state / "available-update.json").read_text()
    assert "LEAK" not in state
    assert json.loads(state)["state"] == "discovery_stale"
    assert (host_paths.current / "VERSION").read_text() == "1.2.0\n"
    with pytest.raises(ReleaseError):
        download_approved_release(release, host_paths, github)


def test_approved_download_verifies_task10_format_then_rejects_replay(host_paths, github):
    from robopark_host.github_releases import (
        check_latest_release,
        download_approved_release,
    )
    from robopark_host.release import ReleaseError

    release = check_latest_release(config(host_paths), github)
    artifact = download_approved_release(release, host_paths, github)
    assert artifact.read_bytes() == github.assets[201]
    assert artifact.stat().st_mode & 0o777 == 0o600
    assert not list(artifact.parent.glob("*.partial"))
    assert (host_paths.current / "VERSION").read_text() == "1.2.0\n"
    with pytest.raises(ReleaseError):
        download_approved_release(release, host_paths, github)
    assert check_latest_release(config(host_paths), github) is None


@pytest.mark.parametrize(
    "mutation",
    [
        "signature",
        "content",
        "internal",
        "changed_id",
        "changed_metadata",
        "oversized",
        "network",
    ],
)
def test_download_failures_clean_partial_and_leave_current_untouched(host_paths, github, mutation):
    from robopark_host.github_releases import (
        check_latest_release,
        download_approved_release,
    )
    from robopark_host.release import ReleaseError

    release = check_latest_release(config(host_paths), github)
    if mutation == "signature":
        github.assets[202] = b"x" * 64
    if mutation == "content":
        github.assets[201] = b"x" * len(github.assets[201])
    if mutation == "internal":
        # A valid outer signature alone must never authorize invalid contents.
        monkey = pytest.MonkeyPatch()
        monkey.setattr(
            "robopark_host.github_releases.verify_archive",
            lambda *a: (_ for _ in ()).throw(ReleaseError("signature_invalid")),
        )
    if mutation == "changed_id":
        github.release["id"] = 102
    if mutation == "changed_metadata":
        github.assets[204] = github.assets[204].replace(b"0017", b"0018")
    if mutation == "oversized":
        github.assets[201] += b"x"
    if mutation == "network":
        github.failure = OSError("LEAK")
    try:
        with pytest.raises(ReleaseError):
            download_approved_release(release, host_paths, github)
    finally:
        if mutation == "internal":
            monkey.undo()
    assert not list((host_paths.state / "github-artifacts").glob("*.partial"))
    assert not list((host_paths.state / "github-artifacts").glob("*.zip"))
    assert (host_paths.current / "VERSION").read_text() == "1.2.0\n"


@pytest.mark.parametrize(
    "left,right,want",
    [
        ("1.10.0", "1.9.0", True),
        ("1.3.0-rc.10", "1.3.0-rc.2", True),
        ("1.3.0", "1.3.0-rc.1", True),
        ("1.3.0+new", "1.3.0+old", False),
        ("1.3.0-rc.1", "1.3.0", False),
    ],
)
def test_semver_order(left, right, want):
    from robopark_host.github_releases import semver

    assert (semver(left) > semver(right)) is want


@pytest.mark.parametrize(
    "version",
    [
        "1.3",
        "01.3.0",
        "1.03.0",
        "1.3.0-01",
        "v1.3.0",
        "1.3.0\n",
        "1.3.0-",
        "1.3.0+",
        "https://evil",
    ],
)
def test_semver_rejects_noncanonical_versions(version):
    from robopark_host.github_releases import semver
    from robopark_host.release import ReleaseError

    with pytest.raises(ReleaseError):
        semver(version)


def test_config_rejects_world_readable_file_and_never_executes_shell(host_paths, github):
    from robopark_host.release import ReleaseError

    path = host_paths.etc / "updater.env"
    path.chmod(0o644)
    with pytest.raises(ReleaseError):
        config(host_paths)
    path.chmod(0o600)
    path.write_text("GITHUB_REPOSITORY=$(touch /tmp/should-not-exist)\n")
    with pytest.raises(ReleaseError):
        config(host_paths)


def test_redirect_boundary_strips_auth_and_rejects_api_and_foreign_redirects():
    from robopark_host.github_releases import AssetRedirects
    from robopark_host.release import ReleaseError

    request = Request(BASE + "/releases/assets/201", headers={"Authorization": "Bearer LEAK"})
    redirected = AssetRedirects(True).redirect_request(
        request,
        None,
        302,
        "",
        {},
        "https://release-assets.githubusercontent.com/path?sig=secret",
    )
    assert redirected.get_header("Authorization") is None
    for url in (
        "http://release-assets.githubusercontent.com/path",
        "https://evil.invalid/path",
        "https://api.github.com/repos/evil/repo",
        "https://release-assets.githubusercontent.com:444/path",
    ):
        with pytest.raises(ReleaseError):
            AssetRedirects(True).redirect_request(request, None, 302, "", {}, url)
    with pytest.raises(ReleaseError):
        AssetRedirects(False).redirect_request(
            request,
            None,
            302,
            "",
            {},
            "https://release-assets.githubusercontent.com/path",
        )


def test_check_cli_publishes_sanitized_configuration_failure(host_paths, monkeypatch):
    from robopark_host.cli import main

    assert main(["check-update"]) == 1
    state = json.loads((host_paths.ops / "public/available-update.json").read_text())
    assert state["state"] == "discovery_stale"


def test_github_host_command_dispatches_verified_artifact_once(host_paths, github, monkeypatch):
    from robopark_host import commands
    from robopark_host.github_releases import check_latest_release
    from robopark_host.state import atomic_write_json

    check_latest_release(config(host_paths), github)
    command = {
        "job_id": str(uuid4()),
        "kind": "github-update",
        "release_id": 101,
        "actor_user_id": 7,
        "created_at": datetime.now(UTC).isoformat(),
    }
    atomic_write_json(host_paths.ops / "inbox/approved.json", command)
    calls = []

    def launcher(paths, path, runner):
        value = json.loads(path.read_text())
        calls.append(value)
        assert (paths.state / "github-artifacts" / value["artifact"]).read_bytes() == github.assets[
            201
        ]
        atomic_write_json(
            paths.ops / "public/rebuild.result",
            {"job_id": value["job_id"], "ok": True},
            mode=0o644,
        )
        return 0

    monkeypatch.setattr("robopark_host.launcher.launch_update", launcher)
    assert (
        commands.consume_commands(
            host_paths, None, None, github_http=github, update_runner=object()
        )
        == 0
    )
    assert calls == [
        {
            "job_id": command["job_id"],
            "kind": "update",
            "artifact": "github-release-101.zip",
            "actor_user_id": 7,
            "created_at": command["created_at"],
        }
    ]
    assert (
        commands.consume_commands(
            host_paths, None, None, github_http=github, update_runner=object()
        )
        == 0
    )
    assert len(calls) == 1


@pytest.mark.parametrize("release_id", [102, True, -1, "101"])
def test_host_rejects_forged_release_id_without_launch(host_paths, github, release_id, monkeypatch):
    from robopark_host import commands
    from robopark_host.github_releases import check_latest_release
    from robopark_host.state import atomic_write_json

    check_latest_release(config(host_paths), github)
    atomic_write_json(
        host_paths.ops / "inbox/approved.json",
        {
            "job_id": str(uuid4()),
            "kind": "github-update",
            "release_id": release_id,
            "actor_user_id": 7,
            "created_at": datetime.now(UTC).isoformat(),
        },
    )
    monkeypatch.setattr(
        "robopark_host.launcher.launch_update",
        lambda *a: pytest.fail("forged approval launched"),
    )
    assert (
        commands.consume_commands(
            host_paths, None, None, github_http=github, update_runner=object()
        )
        == 1
    )


def test_api_cannot_replace_verified_github_artifact_before_worker(host_paths, github):
    from robopark_host.github_releases import (
        check_latest_release,
        download_approved_release,
    )
    from robopark_host.release import UpdateRequest

    release = check_latest_release(config(host_paths), github)
    artifact = download_approved_release(release, host_paths, github)
    public = host_paths.ops / "artifacts"
    public.mkdir(exist_ok=True)
    (public / artifact.name).write_bytes(b"API replacement")
    request = UpdateRequest(str(uuid4()), "update", artifact.name, 7, datetime.now(UTC).isoformat())
    from robopark_host.state import atomic_write_json

    atomic_write_json(artifact.with_suffix(".zip.approval.json"), vars(request))
    assert request.read_artifact(host_paths) == github.assets[201]


def test_task10_prerelease_pack_verify_discover_and_host_validation(host_paths, github, tmp_path):
    import runpy
    from pathlib import Path
    from types import SimpleNamespace

    from robopark_host.github_releases import (
        check_latest_release,
        download_approved_release,
    )
    from robopark_host.release import verify_archive

    root = Path(__file__).resolve().parents[2]
    packer = runpy.run_path(str(root / "scripts/release_pack.py"))
    key = Ed25519PrivateKey.generate()
    private = tmp_path / "prerelease-private.pem"
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    private.chmod(0o600)
    trusted = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    (host_paths.etc / "release-public-key.pem").write_bytes(trusted)
    source = tmp_path / "prerelease"
    source.mkdir()
    (source / "VERSION").write_text("1.3.0-rc.1\n")
    output = tmp_path / "output/robopark-release-1.3.0-rc.1.zip"
    packer["build_release"](
        SimpleNamespace(
            root=source,
            repository=False,
            output=output,
            signing_key=private,
            version="1.3.0-rc.1",
            git_sha="b" * 40,
            migration_head="0017",
        )
    )
    verifier = runpy.run_path(str(root / "scripts/verify-artifact.py"))
    assert verifier["verify_artifact"](output, trusted)["app_version"] == "1.3.0-rc.1"
    from test_github_review import publish_flags

    published, flags = publish_flags(tmp_path, "1.3.0-rc.1")
    assert published.returncode == 0
    github.release.update(
        tag_name=flags[2],
        prerelease="--prerelease" in flags,
        html_url="https://github.com/team/robopark/releases/tag/v1.3.0-rc.1",
    )
    for entry, suffix in zip(
        github.release["assets"], ("", ".sig", ".sha256", ".json"), strict=True
    ):
        raw = Path(str(output) + suffix).read_bytes()
        github.assets[entry["id"]] = raw
        entry.update(name=output.name + suffix, size=len(raw))
    assert check_latest_release(config(host_paths), github) is None
    env = host_paths.etc / "updater.env"
    env.write_text(env.read_text().replace("stable", "prerelease"))
    release = check_latest_release(config(host_paths), github)
    artifact = download_approved_release(release, host_paths, github)
    assert verify_archive(artifact.read_bytes(), trusted).manifest["app_version"] == "1.3.0-rc.1"


def test_release_version_precedence_gates_prerelease_updates():
    from robopark_host.release import ReleaseError, check_compatibility, version

    assert version("1.3.0-rc.10") > version("1.3.0-rc.2")
    assert version("1.3.0") > version("1.3.0-rc.10")
    assert version("1.3.0+build.1") == version("1.3.0+build.2")
    with pytest.raises(ReleaseError):
        version("1.3.0-01")
    current = {"app_version": "1.3.0", "migration_head": "0017"}
    candidate = {
        "app_version": "1.3.0-rc.1",
        "min_installer_version": "0",
        "required_capabilities": [],
        "migration_compatibility": {},
        "migration_head": "0017",
    }
    candidate["files"] = dict.fromkeys(
        (
            "deploy/Dockerfile.api-tests",
            "apps/api/Dockerfile",
            "apps/api/uv.lock",
            "apps/api/pyproject.toml",
            "apps/web/Dockerfile",
            "apps/web/package-lock.json",
            "apps/web/package.json",
            "scripts/verify.sh",
        )
    )
    with pytest.raises(ReleaseError, match="downgrade_rejected"):
        check_compatibility(candidate, current)


def test_release_tag_checker_accepts_prerelease_sources(tmp_path):
    import runpy
    from pathlib import Path

    check = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "scripts/check-release-version.py")
    )["check"]
    version = "1.3.0-rc.1"
    files = {
        "VERSION": version,
        "apps/api/pyproject.toml": '[project]\nversion="1.3.0-rc.1"\n',
        "apps/web/package.json": json.dumps({"version": version}),
        "apps/web/package-lock.json": json.dumps(
            {"version": version, "packages": {"": {"version": version}}}
        ),
        "apps/api/src/robopark_api/services/ops/context.py": 'APP_VERSION = "1.3.0-rc.1"\n',
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    assert check(tmp_path, "v1.3.0-rc.1") == version


@pytest.mark.parametrize(
    "case", ["direct", "oversized_header", "oversized_body", "rate_limit", "truncated"]
)
def test_http_client_bounds_responses_and_uses_private_api_headers(
    host_paths, github, monkeypatch, case
):
    import http.client
    from urllib.error import HTTPError

    from robopark_host.github_releases import GithubHttp, check_latest_release

    class Response(io.BytesIO):
        status = 200
        headers = {}

    class Opener:
        def open(self, request, timeout):
            assert timeout == 15
            assert request.get_header("Authorization") == "Bearer github_pat_LEAK"
            assert request.get_header("User-agent")
            assert request.get_header("Accept") == "application/vnd.github+json"
            if case == "rate_limit":
                raise HTTPError(
                    request.full_url,
                    403,
                    "token=LEAK",
                    {"X-RateLimit-Remaining": "0"},
                    None,
                )
            if case == "truncated":
                raise http.client.IncompleteRead(b"token=LEAK")
            response = Response(b"[]" if case != "oversized_body" else b"x" * (2 * 1024 * 1024 + 1))
            if case == "oversized_header":
                response.headers = {"Content-Length": "999999999999"}
            return response

    monkeypatch.setattr("urllib.request.build_opener", lambda *a: Opener())
    assert check_latest_release(config(host_paths), GithubHttp()) is None
    state = json.loads((host_paths.state / "available-update.json").read_text())
    assert state["state"] == ("up_to_date" if case == "direct" else "discovery_stale")
    assert "LEAK" not in json.dumps(state)


def test_http_binary_asset_direct_200(host_paths, github, monkeypatch):
    from robopark_host.github_releases import GithubHttp

    class Response(io.BytesIO):
        status = 200
        headers = {"Content-Length": "7"}

    class Opener:
        def open(self, request, timeout):
            assert request.get_header("Accept") == "application/octet-stream"
            assert request.get_header("Authorization") == "Bearer private-token"
            return Response(b"ZIPDATA")

    monkeypatch.setattr("urllib.request.build_opener", lambda *a: Opener())
    assert (
        b"".join(
            GithubHttp().iter_bytes(
                BASE + "/releases/assets/201",
                token="private-token",
                asset=True,
                limit=7,
            )
        )
        == b"ZIPDATA"
    )


def test_lower_release_ids_and_changed_immutable_metadata_are_not_reapproved(host_paths, github):
    from robopark_host.github_releases import check_latest_release

    assert check_latest_release(config(host_paths), github).release_id == 101
    github.release["id"] = 100
    github.release["url"] = BASE + "/releases/100"
    assert check_latest_release(config(host_paths), github) is None
    github.release["id"] = 101
    github.release["url"] = BASE + "/releases/101"
    github.assets[204] = github.assets[204].replace(b"a" * 40, b"b" * 40)
    assert check_latest_release(config(host_paths), github) is None


def test_expired_root_approval_is_rejected_without_network(host_paths, github):
    from robopark_host.github_releases import (
        check_latest_release,
        download_approved_release,
    )
    from robopark_host.release import ReleaseError

    release = check_latest_release(config(host_paths), github)
    state = host_paths.state / "available-update.json"
    value = json.loads(state.read_text())
    value["checked_at"] = "2020-01-01T00:00:00+00:00"
    state.write_text(json.dumps(value))
    calls = list(github.calls)
    with pytest.raises(ReleaseError):
        download_approved_release(release, host_paths, github)
    assert github.calls == calls


def test_valid_outer_signature_cannot_hide_invalid_internal_file_hash(host_paths, github):
    import zipfile

    from robopark_host.github_releases import (
        check_latest_release,
        download_approved_release,
    )
    from robopark_host.release import ReleaseError

    key = Ed25519PrivateKey.generate()
    trusted = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    (host_paths.etc / "release-public-key.pem").write_bytes(trusted)
    result = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(github.assets[201])) as source,
        zipfile.ZipFile(result, "w") as target,
    ):
        manifest = source.read("manifest.json")
        # Authenticate the unchanged manifest with the same key as the outer signature.
        canonical = json.dumps(
            json.loads(manifest),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        for name in source.namelist():
            target.writestr(
                name,
                key.sign(canonical)
                if name == "manifest.sig"
                else b"1.9.0\n"
                if name == "VERSION"
                else source.read(name),
            )
    raw = result.getvalue()
    digest = hashlib.sha256(raw).hexdigest()
    github.assets[201], github.assets[202] = raw, key.sign(raw)
    metadata = json.loads(github.assets[204])
    metadata.update(sha256=digest, size=len(raw))
    github.assets[204] = json.dumps(metadata).encode()
    github.assets[203] = f"{digest}  robopark-release-1.3.0.zip\n".encode()
    for entry in github.release["assets"]:
        entry["size"] = len(github.assets[entry["id"]])
    release = check_latest_release(config(host_paths), github)
    assert release is not None
    with pytest.raises(ReleaseError):
        download_approved_release(release, host_paths, github)
    assert not list((host_paths.state / "github-artifacts").glob("*"))


def test_http_slow_stream_has_total_deadline(monkeypatch):
    import itertools

    from robopark_host.github_releases import GithubHttp
    from robopark_host.release import ReleaseError

    clock = itertools.count(0, 20)
    monkeypatch.setattr("robopark_host.github_releases.time.monotonic", lambda: next(clock))

    class Response(io.BytesIO):
        status = 200
        headers = {}

        def read(self, size):
            pytest.fail("read() can block on a slow stream until size bytes arrive")

        def read1(self, size):
            return b"x"

    class Opener:
        def open(self, request, timeout):
            return Response()

    monkeypatch.setattr("urllib.request.build_opener", lambda *a: Opener())
    with pytest.raises(ReleaseError):
        list(
            GithubHttp().iter_bytes(
                BASE + "/releases/assets/201", token="", asset=True, limit=100000
            )
        )


def test_http_truncated_content_length_is_stale(host_paths, github, monkeypatch):
    from robopark_host.github_releases import GithubHttp, check_latest_release

    class Response(io.BytesIO):
        status = 200
        headers = {"Content-Length": "100"}

    class Opener:
        def open(self, request, timeout):
            return Response(b"[]")

    monkeypatch.setattr("urllib.request.build_opener", lambda *a: Opener())
    check_latest_release(config(host_paths), GithubHttp())
    assert (
        json.loads((host_paths.state / "available-update.json").read_text())["state"]
        == "discovery_stale"
    )


def test_worker_reverifies_detached_signature_and_exact_root_approval(host_paths, github):
    from robopark_host.github_releases import check_latest_release, download_approved_release
    from robopark_host.release import ReleaseError, UpdateRequest
    from robopark_host.state import atomic_write_json

    release = check_latest_release(config(host_paths), github)
    artifact = download_approved_release(release, host_paths, github)
    request = UpdateRequest(str(uuid4()), "update", artifact.name, 7, datetime.now(UTC).isoformat())
    with pytest.raises(ReleaseError):
        request.read_artifact(host_paths)
    atomic_write_json(artifact.with_suffix(".zip.approval.json"), vars(request))
    assert request.read_artifact(host_paths) == github.assets[201]
    other = UpdateRequest(str(uuid4()), "update", artifact.name, 7, request.created_at)
    with pytest.raises(ReleaseError):
        other.read_artifact(host_paths)
    artifact.with_suffix(".zip.sig").write_bytes(b"x" * 64)
    with pytest.raises(ReleaseError):
        request.read_artifact(host_paths)


def test_interrupted_github_claim_cleans_partial_without_install(host_paths, github, monkeypatch):
    from robopark_host import commands
    from robopark_host.state import atomic_write_json

    partial = host_paths.state / "github-artifacts/github-release-101.zip.partial"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"incomplete")
    command = {
        "job_id": str(uuid4()),
        "kind": "github-update",
        "release_id": 101,
        "actor_user_id": 7,
        "created_at": datetime.now(UTC).isoformat(),
    }
    atomic_write_json(host_paths.state / "command-request.json", command)
    monkeypatch.setattr(
        "robopark_host.launcher.launch_update",
        lambda *a: pytest.fail("interrupted download installed"),
    )
    assert commands.consume_commands(host_paths, None, None, github_http=github) == 1
    assert not partial.exists()


def test_github_zip_digest_must_match_task10_metadata(host_paths, github):
    from robopark_host.github_releases import check_latest_release

    github.release["assets"][0]["digest"] = "sha256:" + "0" * 64
    assert check_latest_release(config(host_paths), github) is None


def test_nonroot_check_does_not_read_or_write_host_files(host_paths, monkeypatch):
    from dataclasses import replace
    from pathlib import Path

    from robopark_host.github_releases import run_check

    monkeypatch.setattr("robopark_host.github_releases.os.geteuid", lambda: 1000)
    paths = replace(host_paths, root=Path("/"))
    assert run_check(paths) == 1
    assert not paths.state.exists()


def test_asset_transport_rejection_is_stale_not_up_to_date(host_paths, github):
    from robopark_host.github_releases import check_latest_release
    from robopark_host.release import ReleaseError

    original = github.iter_bytes

    def rejected_asset(url, **kwargs):
        if kwargs["asset"]:
            raise ReleaseError("github_release_invalid")  # HTTPS/redirect/body-limit policy
        yield from original(url, **kwargs)

    github.iter_bytes = rejected_asset
    assert check_latest_release(config(host_paths), github) is None
    assert (
        json.loads((host_paths.state / "available-update.json").read_text())["state"]
        == "discovery_stale"
    )
