"""Exercise real packers with disposable signing keys and hostile input trees."""

import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[2]


def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], capture_output=True, text=True, **kwargs)


@pytest.fixture
def packaging(tmp_path):
    key = Ed25519PrivateKey.generate()
    private = tmp_path / "signing.pem"
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    private.chmod(0o600)
    public = tmp_path / "public.pem"
    public.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    source = tmp_path / "source"
    (source / "apps/api").mkdir(parents=True)
    (source / "apps/api/main.py").write_text('print("release")\n')
    (source / "apps/api/alembic/versions").mkdir(parents=True)
    (source / "apps/api/alembic/versions/initial.py").write_text(
        "revision = 'initial'\ndown_revision = None\n"
    )
    (source / "deploy").mkdir()
    (source / "deploy/release-metadata.json").write_text(
        json.dumps(
            {
                "migration_head": "initial",
                "migration_compatibility": {"from_heads": [], "reversible": False},
            }
        )
    )
    env = {
        **os.environ,
        "SOURCE_DATE_EPOCH": "1700000000",
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
    }
    return source, private, public, key, env


def pack(packaging, output):
    source, private, _, _, env = packaging
    return run(
        sys.executable,
        ROOT / "scripts/release_pack.py",
        "--root",
        source,
        "--output",
        output,
        "--version",
        "1.2.3",
        "--git-sha",
        "a" * 40,
        "--migration-head",
        "initial",
        "--signing-key",
        private,
        env=env,
    )


def verify(public, output, script=None):
    return run(
        sys.executable,
        script or ROOT / "scripts/verify-artifact.py",
        "--public-key",
        public,
        output,
        cwd=output.parent,
    )


def test_release_reproducible_with_normalized_zip_and_standalone_verifier(packaging, tmp_path):
    a, b = tmp_path / "a/release.zip", tmp_path / "b/release.zip"
    assert pack(packaging, a).returncode == 0
    os.utime(packaging[0] / "apps/api/main.py", (1800000000, 1800000000))
    (packaging[0] / "apps/api/main.py").chmod(0o700)
    assert pack(packaging, b).returncode == 0
    for suffix in ("", ".sig", ".sha256", ".json"):
        assert Path(str(a) + suffix).read_bytes() == Path(str(b) + suffix).read_bytes()
    with zipfile.ZipFile(a) as archive:
        assert archive.namelist() == sorted(archive.namelist())
        assert {i.date_time for i in archive.infolist()} == {(2023, 11, 14, 22, 13, 20)}
        assert all(stat.S_IMODE(i.external_attr >> 16) == 0o644 for i in archive.infolist())
    standalone = tmp_path / "verify.py"
    shutil.copyfile(ROOT / "scripts/verify-artifact.py", standalone)
    result = verify(packaging[2], a, standalone)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "name",
    [
        ".env.production",
        "secrets.env.local",
        "config.env",
        "private-key.pem",
        "id_ed25519",
        "nested/backup.key",
        "nested/runtime.sqlite3-wal",
        "db.db-shm",
        "log.txt.log",
        "logs/output.txt",
        "diagnostics/bundle.zip",
        "venv/pyvenv.cfg",
        ".cache/item",
        ".git/config",
        "node_modules/item",
        "apps/api/data/unknown.json",
    ],
)
def test_release_excludes_runtime_and_secrets(packaging, tmp_path, name):
    path = packaging[0] / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("MUST_NOT_SHIP")
    output = tmp_path / "release.zip"
    result = pack(packaging, output)
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(output) as archive:
        assert name not in archive.namelist()


@pytest.mark.parametrize(
    "name",
    [
        "apps/web/src/design-system/data/EntityRow.tsx",
        "apps/web/src/domains/diagnostics/DiagnosticRuleEditor.tsx",
    ],
)
def test_release_keeps_source_directories_named_data_or_diagnostics(packaging, tmp_path, name):
    path = packaging[0] / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("export const included = true\n")
    output = tmp_path / "release.zip"

    result = pack(packaging, output)

    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(output) as archive:
        assert name in archive.namelist()


@pytest.mark.parametrize("kind", ["symlink", "fifo", "output_inside", "sidecar_inside"])
def test_release_refuses_unsafe_inputs_before_writing(packaging, tmp_path, kind):
    source = packaging[0]
    output = tmp_path / "release.zip"
    if kind == "symlink":
        (source / "link").symlink_to(source / "apps/api/main.py")
    elif kind == "fifo":
        os.mkfifo(source / "pipe")
    elif kind == "output_inside":
        output = source / "release.zip"
    else:
        Path(str(output) + ".sig").symlink_to(source / "apps/api/main.py")
    result = pack(packaging, output)
    assert result.returncode != 0
    assert not output.exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "signature",
        "checksum",
        "metadata_size",
        "metadata_extra",
        "metadata_duplicate",
        "metadata_bool",
        "internal_hash",
        "alias",
        "traversal",
        "duplicate",
        "oversize",
        "wrong_key",
    ],
)
def test_verifier_rejects_tampering_even_with_valid_outer_signature(packaging, tmp_path, mutation):
    output = tmp_path / "release.zip"
    assert pack(packaging, output).returncode == 0
    public, key = packaging[2:4]
    if mutation in {"internal_hash", "alias", "traversal", "duplicate", "oversize"}:
        data = io.BytesIO()
        with (
            zipfile.ZipFile(output) as old,
            zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED) as new,
        ):
            for info in old.infolist():
                value = old.read(info)
                if mutation == "internal_hash" and info.filename == "apps/api/main.py":
                    value = b"EVIL"
                new.writestr(info, value)
            if mutation != "internal_hash":
                name = {
                    "alias": "apps/./evil",
                    "traversal": "../evil",
                    "duplicate": "apps/api/main.py",
                    "oversize": "bomb",
                }[mutation]
                with (
                    pytest.warns(UserWarning)
                    if mutation == "duplicate"
                    else __import__("contextlib").nullcontext()
                ):
                    new.writestr(name, bytes(1024 * 1024) if mutation == "oversize" else b"evil")
        output.write_bytes(data.getvalue())
        digest = hashlib.sha256(data.getvalue()).hexdigest()
        Path(str(output) + ".sig").write_bytes(key.sign(data.getvalue()))
        Path(str(output) + ".sha256").write_text(f"{digest}  release.zip\n")
        metadata_path = Path(str(output) + ".json")
        metadata = json.loads(metadata_path.read_text())
        metadata.update(sha256=digest, size=len(data.getvalue()))
        metadata_path.write_text(json.dumps(metadata))
    elif mutation == "signature":
        Path(str(output) + ".sig").write_bytes(bytes(64))
    elif mutation == "checksum":
        Path(str(output) + ".sha256").write_text("0" * 64 + "  release.zip\n")
    elif mutation == "wrong_key":
        public.write_bytes(
            Ed25519PrivateKey.generate()
            .public_key()
            .public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        )
    else:
        path = Path(str(output) + ".json")
        metadata = json.loads(path.read_text())
        if mutation == "metadata_duplicate":
            path.write_text(path.read_text().rstrip().removesuffix("}") + ',"size":1}')
        else:
            if mutation == "metadata_size":
                metadata["size"] += 1
            elif mutation == "metadata_extra":
                metadata["unrecognized"] = 1
            else:
                metadata["format"] = True
            path.write_text(json.dumps(metadata))
    assert verify(public, output).returncode != 0


def test_installer_is_reproducible_self_contained_and_does_not_embed_private_key(
    packaging, tmp_path
):
    release = tmp_path / "release.zip"
    assert pack(packaging, release).returncode == 0
    outputs = [tmp_path / name / "installer.tar.gz" for name in ("a", "b")]
    for output in outputs:
        result = run(
            "bash",
            ROOT / "scripts/pack-installer.sh",
            "--release",
            release,
            "--public-key",
            packaging[2],
            "--signing-key",
            packaging[1],
            output,
            env=packaging[4],
        )
        assert result.returncode == 0, result.stderr
    assert outputs[0].read_bytes() == outputs[1].read_bytes()
    with tarfile.open(outputs[0]) as archive:
        names = archive.getnames()
        assert names == sorted(names)
        expected = {
            "START.sh",
            "install.sh",
            "lib/install-release.py",
            "payload/robopark-release.zip",
            "keys/release-public-key.pem",
            "README-RU.txt",
            "verifier/robopark_api/__init__.py",
            "verifier/robopark_api/services/__init__.py",
            "verifier/robopark_api/services/ops/__init__.py",
            "verifier/robopark_api/services/ops/archives.py",
            "verifier/robopark_api/services/ops/release_signing.py",
        }
        assert expected <= set(names)
        for member in archive:
            assert member.isfile()
            assert (member.uid, member.gid, member.uname, member.gname, member.mtime) == (
                0,
                0,
                "",
                "",
                1700000000,
            )
            assert member.mode == (0o755 if member.name.endswith(".sh") else 0o644)
            assert b"PRIVATE KEY" not in archive.extractfile(member).read()
        extracted = tmp_path / "unpacked"
        archive.extractall(extracted, filter="data")
    assert run("sh", extracted / "install.sh", "--help").returncode == 0
    assert run("sh", extracted / "START.sh", "--help").returncode == 0
    assert verify(packaging[2], outputs[0]).returncode == 0


def test_personal_installer_embeds_root_only_preset(packaging, tmp_path):
    release = tmp_path / "release.zip"
    assert pack(packaging, release).returncode == 0
    preset = tmp_path / "preset.env"
    preset.write_text("TUNA_TOKEN=fixture-token\nTUNA_SUBDOMAIN=robopark\n")
    preset.chmod(0o600)
    output = tmp_path / "installer.tar.gz"
    result = run(
        "bash",
        ROOT / "scripts/pack-installer.sh",
        "--release",
        release,
        "--public-key",
        packaging[2],
        "--signing-key",
        packaging[1],
        "--preset",
        preset,
        output,
        env=packaging[4],
    )
    assert result.returncode == 0, result.stderr
    with tarfile.open(output) as archive:
        member = archive.getmember(".robopark-preset.env")
        assert member.mode == 0o600
        assert archive.extractfile(member).read() == preset.read_bytes()


def test_installer_rejects_unsigned_release(packaging, tmp_path):
    release = tmp_path / "release.zip"
    assert pack(packaging, release).returncode == 0
    Path(str(release) + ".sig").write_bytes(bytes(64))
    result = run(
        "bash",
        ROOT / "scripts/pack-installer.sh",
        "--release",
        release,
        "--public-key",
        packaging[2],
        "--signing-key",
        packaging[1],
        tmp_path / "installer.tar.gz",
        env=packaging[4],
    )
    assert result.returncode != 0
    assert not (tmp_path / "installer.tar.gz").exists()


def test_version_sources_match_authoritative_version():
    import ast
    import tomllib

    version = (ROOT / "VERSION").read_text().strip()
    assert json.loads((ROOT / "apps/web/package.json").read_text())["version"] == version
    lock = json.loads((ROOT / "apps/web/package-lock.json").read_text())
    assert lock["version"] == lock["packages"][""]["version"] == version
    assert (
        tomllib.loads((ROOT / "apps/api/pyproject.toml").read_text())["project"]["version"]
        == version
    )
    context = ast.parse((ROOT / "apps/api/src/robopark_api/services/ops/context.py").read_text())
    values = [
        ast.literal_eval(node.value)
        for node in context.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "APP_VERSION" for target in node.targets
        )
    ]
    assert values == [version]


@pytest.mark.parametrize("tag_kind", ["valid", "wrong", "missing_prefix", "injection"])
def test_release_tag_consistency(tag_kind):
    version = (ROOT / "VERSION").read_text().strip()
    tag = {
        "valid": f"v{version}",
        "wrong": "v9.9.9",
        "missing_prefix": version,
        "injection": f"v{version};echo evil",
    }[tag_kind]
    result = run(sys.executable, ROOT / "scripts/check-release-version.py", "--tag", tag)
    assert (result.returncode == 0) == (tag_kind == "valid")


@pytest.mark.parametrize("secret", ["valid", "missing", "invalid", "wrong_key", "ed448"])
def test_ci_key_is_ephemeral_private_and_matches_committed_trust(packaging, tmp_path, secret):
    import base64

    from cryptography.hazmat.primitives.asymmetric.ed448 import Ed448PrivateKey

    key = packaging[1].read_bytes()
    if secret == "missing":
        encoded = ""
    elif secret == "invalid":
        encoded = "invalid-base64-secret"
    else:
        if secret == "wrong_key":
            key = Ed25519PrivateKey.generate().private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        elif secret == "ed448":
            key = Ed448PrivateKey.generate().private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        encoded = base64.b64encode(key).decode()
    output = tmp_path / "ephemeral.pem"
    result = run(
        sys.executable,
        ROOT / "scripts/prepare-release-key.py",
        "--public-key",
        packaging[2],
        "--output",
        output,
        env={**os.environ, "ROBOPARK_RELEASE_SIGNING_KEY_B64": encoded},
    )
    assert (result.returncode == 0) == (secret == "valid")
    assert encoded not in result.stdout + result.stderr if encoded else True
    assert "PRIVATE KEY" not in result.stdout + result.stderr
    if secret == "valid":
        assert stat.S_IMODE(output.stat().st_mode) == 0o600
        assert output.read_bytes() == key
    else:
        assert not output.exists()


def test_repository_packer_refuses_symlinked_source_parent(packaging, tmp_path):
    source = packaging[0]
    external = tmp_path / "external"
    (external / "api").mkdir(parents=True)
    (external / "web").mkdir()
    (external / "api/private.txt").write_text("outside")
    shutil.rmtree(source / "apps")
    (source / "apps").symlink_to(external, target_is_directory=True)
    (source / "deploy").mkdir(exist_ok=True)
    (source / "scripts").mkdir()
    output = tmp_path / "release.zip"
    result = run(
        sys.executable,
        ROOT / "scripts/release_pack.py",
        "--repository",
        "--root",
        source,
        "--output",
        output,
        "--version",
        "1.2.3",
        "--git-sha",
        "a" * 40,
        "--migration-head",
        "initial",
        "--signing-key",
        packaging[1],
        env=packaging[4],
    )
    assert result.returncode != 0
    assert not output.exists()


@pytest.mark.parametrize("kind", ["ed448", "permissions", "disguised_private"])
def test_packer_refuses_wrong_algorithm_or_exposed_private_key(packaging, tmp_path, kind):
    from cryptography.hazmat.primitives.asymmetric.ed448 import Ed448PrivateKey

    if kind == "ed448":
        packaging[1].write_bytes(
            Ed448PrivateKey.generate().private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    elif kind == "permissions":
        packaging[1].chmod(0o644)
    else:
        (packaging[0] / "innocent.txt").write_bytes(packaging[1].read_bytes())
    output = tmp_path / "release.zip"
    assert pack(packaging, output).returncode != 0
    assert not output.exists()


@pytest.mark.parametrize("kind", ["detached", "metadata", "zip_oversize", "fifo"])
def test_verifier_bounds_reads_before_loading_untrusted_input(packaging, tmp_path, kind):
    output = tmp_path / "release.zip"
    assert pack(packaging, output).returncode == 0
    if kind == "detached":
        Path(str(output) + ".sig").write_bytes(bytes(65))
    elif kind == "metadata":
        Path(str(output) + ".json").write_bytes(bytes(4097))
    elif kind == "zip_oversize":
        with output.open("wb") as stream:
            stream.truncate(512 * 1024 * 1024 + 1)
    else:
        output.unlink()
        os.mkfifo(output)
    result = run(
        sys.executable,
        ROOT / "scripts/verify-artifact.py",
        "--public-key",
        packaging[2],
        output,
        timeout=5,
    )
    assert result.returncode != 0


def test_release_workflow_enforces_order_trust_and_cleanup():
    import re

    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    job = workflow["jobs"]["release"]
    steps = job["steps"]
    actions = [s["uses"] for s in steps if "uses" in s]
    assert actions and all(re.fullmatch(r"[^@]+@[a-f0-9]{40}", action) for action in actions)
    names = [s.get("name", "") for s in steps]
    gates = [
        "Check tag and version sources",
        "Full verification gate",
        "Prepare ephemeral signing key",
        "Build architecture-neutral release and installer",
        "Independently verify all publishable artifacts",
        "Publish verified assets",
        "Delete ephemeral signing material",
    ]
    assert [names.index(name) for name in gates] == sorted(names.index(name) for name in gates)
    assert all(not s.get("continue-on-error") for s in steps)
    assert steps[-1]["if"] == "always()"
    assert "rm -f" in steps[-1]["run"]
    secret_steps = [s for s in steps if "secrets." in str(s)]
    assert len(secret_steps) == 1 and secret_steps[0]["name"] == "Prepare ephemeral signing key"
    assert "environment" in job


@pytest.mark.parametrize(
    "mutation", ["bootstrap", "alias", "duplicate", "symlink", "inner_release"]
)
def test_installer_verifier_rejects_unsafe_or_tampered_bundle(packaging, tmp_path, mutation):
    release = tmp_path / "release.zip"
    assert pack(packaging, release).returncode == 0
    output = tmp_path / "installer.tar.gz"
    result = run(
        "bash",
        ROOT / "scripts/pack-installer.sh",
        "--release",
        release,
        "--public-key",
        packaging[2],
        "--signing-key",
        packaging[1],
        output,
        env=packaging[4],
    )
    assert result.returncode == 0, result.stderr
    data = io.BytesIO()
    with tarfile.open(output) as old, tarfile.open(fileobj=data, mode="w:gz") as new:
        for member in old:
            content = old.extractfile(member).read()
            if mutation == "bootstrap" and member.name == "install.sh":
                content = b"evil script\n"
                member.size = len(content)
            if mutation == "inner_release" and member.name == "payload/robopark-release.zip":
                content = b"not a signed release"
                member.size = len(content)
            new.addfile(member, io.BytesIO(content))
        if mutation in {"alias", "duplicate", "symlink"}:
            member = tarfile.TarInfo("lib/../evil" if mutation == "alias" else "install.sh")
            if mutation == "symlink":
                member.name = "lib/evil"
                member.type = tarfile.SYMTYPE
                member.linkname = "/etc/passwd"
            new.addfile(member)
    raw = data.getvalue()
    output.write_bytes(raw)
    if mutation != "bootstrap":
        Path(str(output) + ".sig").write_bytes(packaging[3].sign(raw))
        digest = hashlib.sha256(raw).hexdigest()
        Path(str(output) + ".sha256").write_text(f"{digest}  installer.tar.gz\n")
        metadata_path = Path(str(output) + ".json")
        metadata = json.loads(metadata_path.read_text())
        metadata.update(size=len(raw), sha256=digest)
        metadata_path.write_text(json.dumps(metadata))
    assert verify(packaging[2], output).returncode != 0


@pytest.mark.parametrize(
    "kind,key_name", [("release", "private"), ("installer", "private"), ("installer", "public")]
)
@pytest.mark.parametrize("suffix", ["", ".sig", ".sha256", ".json"])
@pytest.mark.parametrize("alias", ["direct", "parent_symlink", "hardlink"])
def test_all_outputs_reject_key_collisions_before_first_write(
    packaging, tmp_path, kind, key_name, suffix, alias
):
    source, private, public, key, env = packaging
    release = tmp_path / "payload.zip"
    assert pack(packaging, release).returncode == 0
    destination = tmp_path / "destination"
    destination.mkdir()
    output = destination / ("release.zip" if kind == "release" else "installer.tar.gz")
    collided = Path(str(output) + suffix)
    original_key = private if key_name == "private" else public
    original = original_key.read_bytes()
    if alias == "hardlink":
        os.link(original_key, collided)
        protected = original_key
    else:
        collided.write_bytes(original)
        collided.chmod(0o600)
        protected = collided
        if alias == "parent_symlink":
            link = tmp_path / "alias"
            link.symlink_to(destination, target_is_directory=True)
            output = link / output.name
    private = protected if key_name == "private" else private
    public = protected if key_name == "public" else public
    if kind == "release":
        result = pack((source, private, public, key, env), output)
    else:
        result = run(
            "bash",
            ROOT / "scripts/pack-installer.sh",
            "--release",
            release,
            "--public-key",
            public,
            "--signing-key",
            private,
            output,
            env=env,
        )
    assert result.returncode != 0
    assert protected.read_bytes() == original
    assert collided.read_bytes() == original
    for extension in ("", ".sig", ".sha256", ".json"):
        candidate = Path(str(output) + extension)
        if extension != suffix:
            assert not candidate.exists(), "reject before publishing any artifact or sidecar"


@pytest.fixture
def installer_source_copy(tmp_path):
    # A disposable repository layout, containing only trusted files needed by
    # the real packaging entrypoint; no real source files are mutated.
    root = tmp_path / "repository"
    shutil.copytree(
        ROOT / "scripts", root / "scripts", ignore=shutil.ignore_patterns("__pycache__")
    )
    shutil.copytree(
        ROOT / "deploy/installer",
        root / "deploy/installer",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copytree(
        ROOT / "apps/api/src", root / "apps/api/src", ignore=shutil.ignore_patterns("__pycache__")
    )
    return root


@pytest.mark.parametrize(
    "relative",
    [
        "deploy",
        "deploy/installer",
        "deploy/installer/lib",
        "deploy/installer/install.sh",
        "deploy/installer/README-RU.txt",
        "apps",
        "apps/api/src/robopark_api/services/ops",
        "apps/api/src/robopark_api/services/ops/archives.py",
    ],
)
def test_installer_rejects_symlink_components_in_required_sources(
    packaging, installer_source_copy, tmp_path, relative
):
    release = tmp_path / "release.zip"
    assert pack(packaging, release).returncode == 0
    root = installer_source_copy
    original = root / relative
    external = tmp_path / "outside-source"
    original.rename(external)
    original.symlink_to(external, target_is_directory=external.is_dir())
    marker = tmp_path / "outside-code-executed"
    if relative.startswith("apps"):
        verifier = root / "apps/api/src/robopark_api/services/ops/archives.py"
        with verifier.open("a") as stream:
            stream.write(f'\nPath({str(marker)!r}).write_text("outside import")\n')
    output = tmp_path / "installer.tar.gz"
    result = run(
        "bash",
        root / "scripts/pack-installer.sh",
        "--release",
        release,
        "--public-key",
        packaging[2],
        "--signing-key",
        packaging[1],
        output,
        env=packaging[4],
    )
    assert result.returncode != 0
    assert not output.exists()
    assert not marker.exists()


@pytest.mark.parametrize("members,accepted", [(20000, True), (20001, False)])
def test_standalone_verifier_matches_production_zip_member_limit(
    packaging, tmp_path, members, accepted
):
    from robopark_api.services.ops.archives import KIND_RELEASE, ArchiveError, inspect_archive

    payload = b"x"
    names = [f"files/{i:05d}" for i in range(members - 2)]
    manifest = {
        "kind": "release",
        "format": 2,
        "app_version": "1.2.3",
        "git_sha": "a" * 40,
        "migration_head": "initial",
        "min_installer_version": "0",
        "migration_compatibility": {},
        "required_capabilities": [],
        "created_at": "2023-11-14T22:13:20Z",
        "update_notes": "",
        "files": {
            name: {"size": 1, "sha256": hashlib.sha256(payload).hexdigest()} for name in names
        },
    }
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    output = tmp_path / "release.zip"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in names:
            archive.writestr(name, payload)
        archive.writestr("manifest.json", canonical)
        archive.writestr("manifest.sig", packaging[3].sign(canonical))
    raw = output.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    Path(str(output) + ".sig").write_bytes(packaging[3].sign(raw))
    Path(str(output) + ".sha256").write_text(f"{digest}  release.zip\n")
    metadata = {
        "format": 1,
        "kind": "release",
        "filename": "release.zip",
        "size": len(raw),
        "sha256": digest,
        "app_version": "1.2.3",
        "git_sha": "a" * 40,
        "migration_head": "initial",
    }
    Path(str(output) + ".json").write_text(json.dumps(metadata))
    if accepted:
        inspect_archive(raw, expected_kind=KIND_RELEASE, public_key=packaging[2].read_bytes())
    else:
        with pytest.raises(ArchiveError, match="too_many_files"):
            inspect_archive(raw, expected_kind=KIND_RELEASE, public_key=packaging[2].read_bytes())
    assert (verify(packaging[2], output).returncode == 0) is accepted


@pytest.mark.parametrize("input_suffix", ["", ".sig", ".sha256", ".json"])
def test_installer_preserves_all_input_release_files(packaging, tmp_path, input_suffix):
    release = tmp_path / "release.zip"
    assert pack(packaging, release).returncode == 0
    inputs = {
        suffix: Path(str(release) + suffix).read_bytes()
        for suffix in ("", ".sig", ".sha256", ".json")
    }
    output = Path(str(release) + input_suffix)
    result = run(
        "bash",
        ROOT / "scripts/pack-installer.sh",
        "--release",
        release,
        "--public-key",
        packaging[2],
        "--signing-key",
        packaging[1],
        output,
        env=packaging[4],
    )
    assert result.returncode != 0
    for suffix, original in inputs.items():
        assert Path(str(release) + suffix).read_bytes() == original
