"""Private delivery keeps plaintext local and never trusts unauthenticated archives."""

import hashlib
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path
from typing import ClassVar

import pytest
from robopark_host import knowledge_archive as archive
from robopark_host.release import ReleaseError


def test_private_archive_excludes_only_explicit_derivative_paths(tmp_path, monkeypatch):

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    from build_private_knowledge import collect_originals

    kept = {
        "prepared-original/b.txt",
        "records/1.json",
        "attachments/a.jpg",
        "manifest.json",
        "keys.json",
        "scripts/export.py",
    }
    for name in {"prepared/a.txt"} | kept:
        target = tmp_path / "tracker_year_all_parks" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("data")
    files = collect_originals(tmp_path, ["tracker_year_all_parks/prepared"])
    assert {name for name, _ in files} == {
        "originals/tracker_year_all_parks/" + name for name in kept
    }
    for excluded in (
        "../escape",
        "/absolute",
        "export/missing",
        ".",
        "tracker_year_all_parks/records",
        "tracker_year_all_parks/attachments",
        "tracker_year_all_parks/manifest.json",
        "tracker_year_all_parks/keys.json",
        "tracker_year_all_parks/scripts",
    ):
        with pytest.raises(ValueError):
            collect_originals(tmp_path, [excluded])


def pack(entries, *, inventory=None):
    output = io.BytesIO()
    records = (
        inventory
        if inventory is not None
        else [
            {
                "path": name,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            for name, data in entries
        ]
    )
    with tarfile.open(fileobj=output, mode="w:gz") as tar:
        data = json.dumps({"schema": 1, "files": records}).encode()
        info = tarfile.TarInfo("inventory.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
        for name, data in entries:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    output.seek(0)
    return output


def test_authenticated_archive_preserves_bytes_and_restricts_originals(tmp_path):
    source = tmp_path / "part.tar.gz"
    source.write_bytes(
        pack(
            [("bundle/seed.jsonl", b"hello"), ("originals/chat/a.txt", b"private")]
        ).getvalue()
    )
    root = tmp_path / "stage"
    root.mkdir(mode=0o700)
    seen = set()
    archive.extract_part(source, root, seen, max_bytes=20, max_files=2)
    assert (root / "originals/chat/a.txt").read_bytes() == b"private"
    assert (root / "originals/chat/a.txt").stat().st_mode & 0o777 == 0o600
    assert seen == {"bundle/seed.jsonl", "originals/chat/a.txt"}


@pytest.mark.parametrize(
    "name",
    ["../escaped", "/tmp/escaped", "bundle/../../escaped", "bundle//bad", "other/file"],
)
def test_archive_rejects_escape_and_unexpected_roots(tmp_path, name):
    source = tmp_path / "part.tar.gz"
    source.write_bytes(pack([(name, b"x")]).getvalue())
    with pytest.raises(ReleaseError, match="knowledge_archive_invalid"):
        archive.extract_part(
            source, tmp_path / "stage", set(), max_bytes=10, max_files=2
        )
    assert not (tmp_path / "escaped").exists()


def test_archive_rejects_duplicates_hash_mismatch_and_budget(tmp_path):
    for blob, budget in [
        (pack([("bundle/a", b"a"), ("bundle/a", b"a")]), 10),
        (
            pack(
                [("bundle/a", b"a")],
                inventory=[{"path": "bundle/a", "bytes": 1, "sha256": "0" * 64}],
            ),
            10,
        ),
        (pack([("bundle/a", b"long")]), 1),
    ]:
        source = tmp_path / "part.tar.gz"
        source.write_bytes(blob.getvalue())
        with pytest.raises(ReleaseError, match="knowledge_archive_invalid"):
            archive.extract_part(
                source, tmp_path / "stage", set(), max_bytes=budget, max_files=2
            )


def test_archive_rejects_symlink_even_with_inventory(tmp_path):
    source = tmp_path / "bad.tar.gz"
    with tarfile.open(source, "w:gz") as tar:
        blob = json.dumps(
            {
                "schema": 1,
                "files": [
                    {
                        "path": "bundle/link",
                        "bytes": 0,
                        "sha256": hashlib.sha256(b"").hexdigest(),
                    }
                ],
            }
        ).encode()
        item = tarfile.TarInfo("inventory.json")
        item.size = len(blob)
        tar.addfile(item, io.BytesIO(blob))
        item = tarfile.TarInfo("bundle/link")
        item.type = tarfile.SYMTYPE
        item.linkname = "/etc/passwd"
        tar.addfile(item)
    with pytest.raises(ReleaseError, match="knowledge_archive_invalid"):
        archive.extract_part(
            source, tmp_path / "stage", set(), max_bytes=100, max_files=2
        )


def test_archive_rejects_missing_and_cross_part_duplicate(tmp_path):
    source = tmp_path / "part.tar.gz"
    source.write_bytes(pack([("bundle/a", b"a")]).getvalue())
    with pytest.raises(ReleaseError, match="knowledge_archive_invalid"):
        archive.extract_part(
            source, tmp_path / "stage", {"bundle/a"}, max_bytes=100, max_files=2
        )
    source.write_bytes(
        pack(
            [], inventory=[{"path": "bundle/a", "bytes": 1, "sha256": "0" * 64}]
        ).getvalue()
    )
    with pytest.raises(ReleaseError, match="knowledge_archive_invalid"):
        archive.extract_part(
            source, tmp_path / "stage", set(), max_bytes=100, max_files=2
        )


def test_manifest_rejects_external_urls_and_unbounded_parts():
    from robopark_host.knowledge_assets import validate_manifest

    good = {
        "schema": 1,
        "id": "repair-20261005-v2",
        "format": "age-tar-gzip-v1",
        "documents": 15595,
        "files": 2,
        "expanded_bytes": 12,
        "parts": [
            {
                "name": "part-0001.tar.gz.age",
                "bytes": 32,
                "sha256": "1" * 64,
                "files": 2,
                "expanded_bytes": 12,
            }
        ],
        "base_url": "https://github.com/tehblok/robopark/releases/download/knowledge-20261005-v2",
    }
    assert validate_manifest(good) == good
    for update in [
        {"base_url": "https://other.invalid/archive"},
        {"expanded_bytes": -1},
        {"parts": [{**good["parts"][0], "name": "../file"}]},
        {"parts": good["parts"] * 2},
        {"files": 1},
        {"schema": True},
    ]:
        with pytest.raises(ReleaseError, match="knowledge_manifest_invalid"):
            validate_manifest({**good, **update})


def test_download_resumes_and_rejects_wrong_hash(tmp_path, monkeypatch):
    from robopark_host import knowledge_assets as assets

    blob = b"0123456789"
    target = tmp_path / "asset"
    target.with_suffix(".partial").write_bytes(blob[:4])

    class Response(io.BytesIO):
        status = 206
        headers: ClassVar[dict] = {"Content-Range": "bytes 4-9/10"}

        def geturl(self):
            return "https://github.com/asset"

    calls = []

    def opened(request, **kwargs):
        calls.append(request.get_header("Range"))
        return Response(blob[4:])

    monkeypatch.setattr(assets, "open_url", opened)
    assets.download(
        "https://github.com/asset", target, len(blob), hashlib.sha256(blob).hexdigest()
    )
    assert target.read_bytes() == blob
    assert calls == ["bytes=4-"]


def test_download_restarts_when_server_ignores_range(tmp_path, monkeypatch):
    from robopark_host import knowledge_assets as assets

    blob = b"0123456789"
    target = tmp_path / "asset"
    target.with_suffix(".partial").write_bytes(blob[:4])

    class Response(io.BytesIO):
        status = 200
        headers: ClassVar[dict] = {}

        def geturl(self):
            return "https://github.com/asset"

    monkeypatch.setattr(assets, "open_url", lambda *args, **kw: Response(blob))
    assets.download(
        "https://github.com/asset", target, len(blob), hashlib.sha256(blob).hexdigest()
    )
    assert target.read_bytes() == blob


def test_download_caps_response_and_refuses_symlinks(tmp_path, monkeypatch):
    from robopark_host import knowledge_assets as assets

    class Response(io.BytesIO):
        status = 200
        headers: ClassVar[dict] = {}

        def geturl(self):
            return "https://github.com/asset"

    monkeypatch.setattr(assets, "open_url", lambda *args, **kw: Response(b"overflow"))
    with pytest.raises(ReleaseError, match="knowledge_download_size"):
        assets.download("https://github.com/asset", tmp_path / "asset", 2, "0" * 64)
    outside = tmp_path / "outside"
    outside.write_text("untouched")
    (tmp_path / "linked.partial").symlink_to(outside)
    with pytest.raises(ReleaseError, match="knowledge_cache_invalid"):
        assets.download("https://github.com/asset", tmp_path / "linked", 2, "0" * 64)
    assert outside.read_text() == "untouched"


def test_unsupported_host_never_downloads_or_prompts(host_paths, monkeypatch):
    from robopark_host import knowledge_delivery as delivery

    monkeypatch.setattr(
        delivery, "probe_support", lambda paths: (False, "p3701_required")
    )
    monkeypatch.setattr(
        delivery, "load_manifest", lambda paths: pytest.fail("must not inspect assets")
    )
    assert delivery.install_knowledge(host_paths) is False


def test_bad_identity_does_not_invoke_external_process(tmp_path, monkeypatch):
    from robopark_host.knowledge_crypto import decrypt_part

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kw: pytest.fail("invalid key must be rejected"),
    )
    for key in ["age-plugin-bogus", "AGE-SECRET-KEY-1ABC\nexec", ""]:
        with pytest.raises(ReleaseError, match="knowledge_identity_invalid"):
            decrypt_part(
                Path("/bin/false"),
                tmp_path / "encrypted",
                tmp_path / "plain",
                key,
                tmp_path / "keys",
            )


def test_real_age_roundtrip_and_tamper_leave_no_plaintext(tmp_path):
    from robopark_host.knowledge_crypto import decrypt_part

    binary = os.environ.get("ROBOPARK_TEST_AGE")
    if not binary:
        pytest.skip("set ROBOPARK_TEST_AGE for official age binary acceptance")
    binary = Path(binary)
    identity_path = tmp_path / "identity"
    subprocess.run(
        [str(binary.with_name("age-keygen")), "-o", str(identity_path)],
        check=True,
        capture_output=True,
    )
    key = next(
        x
        for x in identity_path.read_text().splitlines()
        if x.startswith("AGE-SECRET-KEY-")
    )
    recipient = subprocess.check_output(
        [str(binary.with_name("age-keygen")), "-y", str(identity_path)], text=True
    ).strip()
    encrypted = tmp_path / "part.age"
    subprocess.run(
        [str(binary), "-r", recipient, "-o", str(encrypted)],
        input=b"original bytes",
        check=True,
    )
    plain = tmp_path / "plain"
    decrypt_part(binary, encrypted, plain, key, tmp_path / "keys")
    assert plain.read_bytes() == b"original bytes"
    assert not list((tmp_path / "keys").iterdir())
    plain.unlink()
    broken = bytearray(encrypted.read_bytes())
    broken[-1] ^= 1
    encrypted.write_bytes(broken)
    with pytest.raises(ReleaseError, match="knowledge_decryption_failed"):
        decrypt_part(binary, encrypted, plain, key, tmp_path / "keys")
    assert not plain.exists()
    assert not list((tmp_path / "keys").iterdir())


def test_full_bundle_reuses_public_identity_and_covers_all_sources(tmp_path):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import hmac

    from build_private_knowledge import build_bundle

    key = b"a" * 32
    ref = "source:v2:0123456789abcdefabcd#1"
    public_ref = (
        "public:repair-v1:"
        + hmac.new(
            key, ("robopark-public-repair-v1\0" + ref).encode(), hashlib.sha256
        ).hexdigest()[:32]
    )
    private = tmp_path / "seed.jsonl"
    records = [
        {
            "title": "Full repair",
            "content": "Private detailed evidence",
            "kind": "ticket",
            "source_ref": ref,
        },
        {
            "title": "Chat",
            "content": "Unverified repair suggestion",
            "kind": "chat",
            "source_ref": ref + "2",
        },
    ]
    private.write_text("".join(json.dumps(r) + "\n" for r in records))
    public = tmp_path / "public.jsonl"
    public.write_text(
        json.dumps(
            {**records[0], "content": "public excerpt", "source_ref": public_ref}
        )
        + "\n"
    )
    output = tmp_path / "bundle"
    result = build_bundle(private, public, key, output)
    rows = [json.loads(line) for line in (output / "seed.jsonl").open()]
    assert result["documents"] == 2
    assert rows[0]["source_ref"] == public_ref
    assert rows[0]["content"] == "Private detailed evidence"
    assert rows[1]["source_ref"].startswith("private:repair-v2:")
    assert set(rows[1]) == {"title", "content", "source_ref", "kind"}
    assert result["bundle_id"] == "repair-private-v2"


def test_installer_wrong_key_keeps_prior_current_and_retry_is_idempotent(
    host_paths, monkeypatch, tmp_path
):
    import sys

    from robopark_host import knowledge_delivery as delivery

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from build_private_knowledge import package_part

    binary = os.environ.get("ROBOPARK_TEST_AGE")
    if not binary:
        pytest.skip("official age acceptance")
    binary = Path(binary)
    keys = []
    for index in range(2):
        path = tmp_path / f"key-{index}"
        subprocess.run(
            [str(binary.with_name("age-keygen")), "-o", str(path)],
            check=True,
            capture_output=True,
        )
        keys.append(
            next(
                x
                for x in path.read_text().splitlines()
                if x.startswith("AGE-SECRET-KEY-")
            )
        )
    recipient = subprocess.check_output(
        [str(binary.with_name("age-keygen")), "-y", str(tmp_path / "key-0")], text=True
    ).strip()
    seed = tmp_path / "seed.jsonl"
    seed.write_text(
        json.dumps(
            {
                "title": "Case",
                "content": "Evidence",
                "kind": "chat",
                "source_ref": "private:repair-v2:" + "1" * 32,
            }
        )
        + "\n"
    )
    bm = tmp_path / "bundle.json"
    bm.write_text(
        json.dumps(
            {
                "schema": 1,
                "bundle_id": "repair-private-v2",
                "documents": 1,
                "parts": [
                    {
                        "path": "seed.jsonl",
                        "bytes": seed.stat().st_size,
                        "documents": 1,
                        "sha256": hashlib.sha256(seed.read_bytes()).hexdigest(),
                    }
                ],
            }
        )
    )
    original = tmp_path / "source"
    original.write_bytes(b"Original evidence")
    part = package_part(
        [
            ("bundle/seed.jsonl", seed),
            ("bundle/manifest.json", bm),
            ("originals/a.txt", original),
        ],
        tmp_path / "part-0001.tar.gz.age",
        binary,
        recipient,
    )
    manifest = {
        "schema": 1,
        "id": "repair-fixture",
        "format": "age-tar-gzip-v1",
        "documents": 1,
        "files": 3,
        "expanded_bytes": part["expanded_bytes"],
        "parts": [part],
        "base_url": "https://github.com/tehblok/robopark/releases/download/knowledge-fixture",
    }
    monkeypatch.setattr(delivery, "probe_support", lambda paths: (True, None))
    monkeypatch.setattr(delivery, "load_manifest", lambda paths: manifest)
    monkeypatch.setattr(delivery, "ensure_age", lambda cache: binary)

    def copy_asset(url, target, size, digest):
        target.write_bytes((tmp_path / part["name"]).read_bytes())
        return target

    monkeypatch.setattr(delivery, "download", copy_asset)
    base = host_paths.var / "knowledge"
    base.mkdir(parents=True, mode=0o700)
    (base / "current").symlink_to("versions/prior")
    (base / "versions").mkdir(mode=0o700)
    abandoned = base / "versions/.stage-interrupted"
    abandoned.mkdir(mode=0o700)
    (abandoned / "partial").write_bytes(b"old")
    with pytest.raises(ReleaseError, match="knowledge_decryption_failed"):
        delivery.install_knowledge(host_paths, identity=keys[1])
    assert os.readlink(base / "current") == "versions/prior"
    assert not list((base / "versions").glob(".stage-*"))
    assert delivery.install_knowledge(host_paths, identity=keys[0])
    assert (base / "current/originals/a.txt").read_bytes() == original.read_bytes()
    assert (base / "current/bundle/seed.jsonl").stat().st_mode & 0o777 == 0o644
    assert (base / "current/originals/a.txt").stat().st_mode & 0o777 == 0o600
    monkeypatch.setattr(
        delivery, "download", lambda *a, **kw: pytest.fail("already installed")
    )
    monkeypatch.setattr(
        delivery.getpass, "getpass", lambda *a, **kw: pytest.fail("no second key entry")
    )
    # Crash after publication must not leave a second corpus-sized cache.
    cache = next((base / "downloads").iterdir())
    (cache / part["name"]).write_bytes(b"leftover")
    assert delivery.install_knowledge(host_paths)
    assert not (cache / part["name"]).exists()
    assert not list((host_paths.root / "run/robopark-knowledge-keys").iterdir())
    # Corrupt installed seed is repaired atomically, preserving prior originals.
    previous = (base / "current").resolve()
    (previous / "bundle/seed.jsonl").write_bytes(b"broken")
    monkeypatch.setattr(delivery, "download", copy_asset)
    assert delivery.install_knowledge(host_paths, identity=keys[0])
    assert (base / "current").resolve() != previous
    assert (previous / "originals/a.txt").read_bytes() == original.read_bytes()
    assert (base / "current/bundle/seed.jsonl").read_bytes() == seed.read_bytes()


def test_oversized_pax_header_is_bounded_before_inventory(tmp_path, monkeypatch):
    import gzip

    monkeypatch.setattr(archive, "MAX_INVENTORY", 1024)
    source = tmp_path / "pax.tar.gz"
    info = tarfile.TarInfo("pax")
    info.type = tarfile.XHDTYPE
    info.size = 3 * 1024 * 1024
    with gzip.open(source, "wb") as stream:
        stream.write(info.tobuf())
        stream.write(b"\0" * info.size)
        stream.write(b"\0" * 1024)
    with pytest.raises(ReleaseError, match="knowledge_archive_invalid"):
        archive.extract_part(
            source, tmp_path / "stage", set(), max_bytes=1, max_files=1
        )


def test_disk_budget_subtracts_only_expected_cached_bytes(tmp_path):
    from robopark_host.knowledge_delivery import required_space

    part = {"name": "part-0001.tar.gz.age", "bytes": 1000}
    manifest = {"expanded_bytes": 5000, "parts": [part]}
    cache = tmp_path / "cache"
    cache.mkdir()
    full = required_space(manifest, cache)
    (cache / part["name"]).with_suffix(".partial").write_bytes(b"x" * 300)
    assert required_space(manifest, cache) == full - 300
    (cache / part["name"]).write_bytes(b"x" * 1000)
    assert required_space(manifest, cache) == full - 1000


def test_missing_source_tree_cannot_silently_build_incomplete_archive(tmp_path):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from build_private_knowledge import collect

    with pytest.raises(ValueError, match="source_missing"):
        collect(tmp_path / "missing", "originals")


def test_completed_corrupt_download_is_replaced(tmp_path, monkeypatch):
    from robopark_host import knowledge_assets as assets

    blob = b"correct"
    target = tmp_path / "asset"
    target.write_bytes(b"corrupt")

    class Response(io.BytesIO):
        status = 200
        headers: ClassVar[dict] = {}

        def geturl(self):
            return "https://github.com/asset"

    monkeypatch.setattr(assets, "open_url", lambda *a, **kw: Response(blob))
    assets.download(
        "https://github.com/asset", target, len(blob), hashlib.sha256(blob).hexdigest()
    )
    assert target.read_bytes() == blob
