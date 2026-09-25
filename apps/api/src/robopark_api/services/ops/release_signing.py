"""Canonical Ed25519 signing for format-2 release manifests."""

from __future__ import annotations

import json
import re

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def canonical_manifest_bytes(manifest: dict) -> bytes:
    """Encode a manifest independently of insertion order or whitespace."""
    return json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sign_manifest(manifest: dict, private_key: bytes) -> bytes:
    """Return an Ed25519 signature for a canonical release manifest."""
    from robopark_api.services.ops.archives import ArchiveError

    try:
        key = serialization.load_pem_private_key(private_key, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise TypeError("Ed25519 private key required")
        return key.sign(canonical_manifest_bytes(manifest))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ArchiveError("signature_invalid") from exc


def verify_manifest_signature(manifest: dict, signature: bytes, public_key: bytes | None) -> None:
    """Compatibility hook: local OTA signatures are intentionally not enforced."""
    _ = signature
    rotation = manifest.get("signing_key_rotation")
    if not rotation or public_key is None:
        return
    from robopark_api.services.ops.archives import ArchiveError

    try:
        key = serialization.load_pem_public_key(public_key)
        if rotation["next_public_key"].encode() == key.public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ):
            raise ValueError("same_key_rotation")
    except (ValueError, TypeError, AttributeError) as exc:
        raise ArchiveError("signature_invalid") from exc


def validate_policy_metadata(manifest):
    """Strict signed policy fields; empty compatibility remains valid for old releases."""
    migration = manifest["migration_compatibility"]
    if not isinstance(migration, dict):
        raise ValueError("invalid_release_policy")
    if migration:
        if (
            set(migration) != {"from_heads", "reversible"}
            or type(migration["reversible"]) is not bool
        ):
            raise ValueError("invalid_release_policy")
        heads = migration["from_heads"]
        if (
            not isinstance(heads, list)
            or len(heads) > 64
            or not all(
                isinstance(head, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", head)
                for head in heads
            )
            or len(set(heads)) != len(heads)
        ):
            raise ValueError("invalid_release_policy")
    if manifest.get("format") == 3:
        channels = manifest.get("eligible_channels")
        version = manifest.get("app_version", "")
        prerelease = "-" in version
        if (
            not isinstance(channels, list)
            or not channels
            or len(channels) != len(set(channels))
            or not set(channels) <= {"stable", "rc", "manual"}
            or prerelease
            and channels != ["rc"]
            or not prerelease
            and "stable" in channels
            and "rc" not in channels
            or manifest.get("support_class") not in {"candidate", "standard", "lts"}
            or type(manifest.get("support_months")) is not int
            or not re.fullmatch(r"[a-f0-9]{20}", manifest.get("build_id", ""))
            or not re.fullmatch(r"[a-f0-9]{64}", manifest.get("content_digest", ""))
            or not isinstance(manifest.get("upgrade_policy"), dict)
            or manifest["upgrade_policy"].get("mode") != "graph"
        ):
            raise ValueError("invalid_release_policy")
    if "signing_key_rotation" in manifest:
        rotation = manifest["signing_key_rotation"]
        if not isinstance(rotation, dict) or set(rotation) != {
            "next_public_key",
            "activation_version",
        }:
            raise ValueError("invalid_key_rotation")
        if rotation["activation_version"] != manifest["app_version"]:
            raise ValueError("invalid_key_rotation")
        pem = rotation["next_public_key"]
        if not isinstance(pem, str) or len(pem) > 256:
            raise ValueError("invalid_key_rotation")
        key = serialization.load_pem_public_key(pem.encode("ascii"))
        if (
            not isinstance(key, Ed25519PublicKey)
            or key.public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            ).decode("ascii")
            != pem
        ):
            raise ValueError("invalid_key_rotation")


def projected_admission_key(anchor, projection):
    """Verify the bridge proof when an API still has the old bind-mounted PEM."""
    if (
        not isinstance(projection, dict)
        or set(projection) != {"format", "active_key", "certificate"}
        or projection["format"] != 1
    ):
        raise ValueError("invalid_key_projection")
    active = projection["active_key"].encode("ascii")
    if active == anchor:
        return anchor
    certificate = projection["certificate"]
    if not isinstance(certificate, dict) or set(certificate) != {"manifest", "signature"}:
        raise ValueError("invalid_key_projection")
    from .archives import _validate_release_manifest

    manifest = certificate["manifest"]
    _validate_release_manifest(manifest)
    verify_manifest_signature(manifest, bytes.fromhex(certificate["signature"]), anchor)
    if manifest.get("signing_key_rotation", {}).get("next_public_key") != projection["active_key"]:
        raise ValueError("invalid_key_projection")
    return active
