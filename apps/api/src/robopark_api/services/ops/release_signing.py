"""Canonical Ed25519 signing for format-2 release manifests."""

from __future__ import annotations

import json
import re

from cryptography.exceptions import InvalidSignature
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


def verify_manifest_signature(manifest: dict, signature: bytes, public_key: bytes) -> None:
    """Verify ``signature`` or expose one stable archive error token."""
    from robopark_api.services.ops.archives import ArchiveError

    try:
        key = serialization.load_pem_public_key(public_key)
        if not isinstance(key, Ed25519PublicKey):
            raise TypeError("Ed25519 public key required")
        key.verify(signature, canonical_manifest_bytes(manifest))
    except (ValueError, TypeError, AttributeError, InvalidSignature) as exc:
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
