"""Validate immutable shipped knowledge once, then import bounded resumable batches."""

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select

from robopark_api.ai_models import AIBundleDocument, AIDocument
from robopark_api.ai_schemas import DocumentIn
from robopark_api.models import PlatformSetting
from robopark_api.services.ai import knowledge, policy
from robopark_api.services.database_locks import database_idempotency_lock

KEY = "ai.knowledge.bundle"
MAX_BYTES = 64 * 1024 * 1024
MAX_DOCUMENTS = 50_000
MAX_LINE = 512 * 1024
FIELDS = {"title", "content", "kind", "source_ref"}


@dataclass(frozen=True)
class Bundle:
    root: str
    revision: str
    bundle_id: str
    parts: tuple[str, ...]
    stamps: tuple[tuple[int, int, int, int], ...]
    offsets: tuple[tuple[int, int], ...]


def _empty(state="pending", error=None):
    return {"state": state, "total": 0, "processed": 0, "created": 0, "skipped": 0, "error": error}


def _saved(db):
    row = db.get(PlatformSetting, KEY)
    if row:
        try:
            value = json.loads(row.value)
            if isinstance(value, dict):
                return value
        except (ValueError, TypeError):
            pass
    return _empty()


def _save(db, value):
    encoded = json.dumps(value, sort_keys=True)
    row = db.get(PlatformSetting, KEY)
    if row is None:
        db.add(PlatformSetting(key=KEY, value=encoded))
    elif row.value != encoded:
        row.value = encoded


def status(db, settings):
    if not policy.host_status(settings)["supported"]:
        return _empty("unavailable")
    value = _saved(db)
    if not policy.config(db)["enabled"]:
        value = {**value, "state": "paused"}
    return {key: value.get(key, default) for key, default in _empty().items()}


def _stamp(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("ai_bundle_invalid")
    stat = path.stat()
    return stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _record(raw, bundle_id):
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError("ai_bundle_invalid")
    parsed = DocumentIn.model_validate(value)
    patterns = {
        "repair-public-v1": r"public:repair-v1:[a-f0-9]{32}",
        "repair-private-v2": r"(?:public:repair-v1|private:repair-v2):[a-f0-9]{32}",
    }
    if not re.fullmatch(patterns[bundle_id], parsed.source_ref):
        raise ValueError("ai_bundle_invalid")
    value = parsed.model_dump()
    value["title"] = knowledge.redact(value["title"])[:250]
    value["content"] = knowledge.redact(value["content"])
    value["source_ref"] = knowledge.redact(value["source_ref"])[:400]
    if not value["title"] or not value["content"]:
        raise ValueError("ai_bundle_invalid")
    return value


def _manifest(raw):
    value = json.loads(raw)
    if (
        not isinstance(value, dict)
        or value.get("schema") != 1
        or value.get("bundle_id") not in {"repair-public-v1", "repair-private-v2"}
        or type(value.get("documents")) is not int
        or not 0 < value["documents"] <= MAX_DOCUMENTS
        or not isinstance(value.get("parts"), list)
        or not 1 <= len(value["parts"]) <= 128
    ):
        raise ValueError("ai_bundle_invalid")
    names = set()
    for part in value["parts"]:
        if (
            not isinstance(part, dict)
            or set(part) != {"path", "bytes", "documents", "sha256"}
            or not isinstance(part["path"], str)
            or not re.fullmatch(r"(?:seed|part-\d{4})\.jsonl", part["path"])
            or part["path"] in names
            or type(part["bytes"]) is not int
            or not 0 < part["bytes"] <= MAX_BYTES
            or type(part["documents"]) is not int
            or not 0 < part["documents"] <= MAX_DOCUMENTS
            or not isinstance(part["sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", part["sha256"])
        ):
            raise ValueError("ai_bundle_invalid")
        names.add(part["path"])
    if (
        sum(p["bytes"] for p in value["parts"]) > MAX_BYTES
        or sum(p["documents"] for p in value["parts"]) != value["documents"]
    ):
        raise ValueError("ai_bundle_invalid")
    return value


@lru_cache(maxsize=2)
def _validated(root, raw, stamps):
    # Cache offsets only, not the corpus text or ORM objects. Failed immutable
    # snapshots are cached too, so a corrupt package cannot busy-loop on disk.
    try:
        manifest = _manifest(raw)
        offsets, refs = [], set()
        for index, part in enumerate(manifest["parts"]):
            path = Path(root) / part["path"]
            digest, size, count = hashlib.sha256(), 0, 0
            with path.open("rb") as handle:
                while line := handle.readline(MAX_LINE + 1):
                    if len(line) > MAX_LINE:
                        raise ValueError("ai_bundle_invalid")
                    value = _record(line, manifest["bundle_id"])
                    if value["source_ref"] in refs:
                        raise ValueError("ai_bundle_invalid")
                    refs.add(value["source_ref"])
                    offsets.append((index, size))
                    size += len(line)
                    count += 1
                    digest.update(line)
                    if size > part["bytes"] or count > part["documents"]:
                        raise ValueError("ai_bundle_invalid")
            if (
                size != part["bytes"]
                or count != part["documents"]
                or digest.hexdigest() != part["sha256"]
                or _stamp(path) != stamps[index]
            ):
                raise ValueError("ai_bundle_invalid")
        return Bundle(
            root,
            hashlib.sha256(raw).hexdigest(),
            manifest["bundle_id"],
            tuple(p["path"] for p in manifest["parts"]),
            stamps,
            tuple(offsets),
        ), None
    except (OSError, ValueError, TypeError, KeyError, ValidationError):
        return None, "ai_bundle_invalid"


def inspect(settings):
    root = Path(settings.ai_knowledge_bundle_path).resolve()
    path = root / "manifest.json"
    try:
        if _stamp(path)[1] > 64 * 1024:
            raise ValueError("ai_bundle_invalid")
        with path.open("rb") as handle:
            raw = handle.read(64 * 1024 + 1)
        if len(raw) > 64 * 1024:
            raise ValueError("ai_bundle_invalid")
        manifest = _manifest(raw)
        stamps = tuple(_stamp(root / p["path"]) for p in manifest["parts"])
        return _validated(str(root), raw, stamps)
    except FileNotFoundError:
        return None, "ai_bundle_unavailable"
    except (OSError, ValueError, TypeError, KeyError):
        return None, "ai_bundle_invalid"


def _document_signature(row):
    value = {
        "title": row.title,
        "content": row.content,
        "kind": row.kind,
        "source_ref": row.source_ref,
        "state": row.state,
        "trust": row.trust,
    }
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _read_batch(package, start, end):
    records = []
    for index, offset in package.offsets[start:end]:
        path = Path(package.root) / package.parts[index]
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            stamp = (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            if stamp != package.stamps[index]:
                raise ValueError("ai_bundle_invalid")
            handle.seek(offset)
            raw = handle.readline(MAX_LINE + 1)
            data = _record(raw, package.bundle_id)
            after = os.fstat(handle.fileno())
            if (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != stamp:
                raise ValueError("ai_bundle_invalid")
            records.append(data)
    return records


def _bundle_values(data):
    return {
        "title": data["title"],
        "content": data["content"],
        "kind": data["kind"],
        "source_ref": data["source_ref"],
        "state": "active",
        "trust": "instruction" if data["kind"] == "manual" else "unverified",
    }


def _adoptable(row, data):
    # A legacy importer created pristine global rows at revision 1. Any other
    # shape may encode an operator decision made before receipts existed.
    desired = _bundle_values(data)
    return (
        row.created_by is None
        and row.park_id is None
        and row.revision == 1
        and all(getattr(row, key) == value for key, value in desired.items())
        and row.fingerprint == hashlib.sha256(("None\n" + desired["content"]).encode()).hexdigest()
    )


def _apply_record(db, package, data):
    source_ref = data["source_ref"]
    receipt = db.get(AIBundleDocument, source_ref)
    if receipt is None:
        existing = list(
            db.scalars(
                select(AIDocument)
                .where(AIDocument.source_ref == source_ref, AIDocument.park_id.is_(None))
                .limit(2)
            )
        )
        if not existing:
            desired = {**data, "park_id": None, "state": "active"}
            row, created = knowledge._add_document(db, desired, created_by=None, stable_source=True)
        else:
            if len(existing) != 1 or not _adoptable(existing[0], data):
                return False
            row = existing[0]
            created = False
        receipt = AIBundleDocument(
            source_ref=source_ref,
            document_id=row.id,
            applied_document_revision=row.revision,
            applied_signature=_document_signature(row),
            applied_bundle_revision=package.revision,
            seen_bundle_revision=package.revision,
        )
        db.add(receipt)
        return created

    row = db.get(AIDocument, receipt.document_id)
    if row is None:
        raise ValueError("ai_bundle_receipt_invalid")
    unchanged = (
        row.revision == receipt.applied_document_revision
        and _document_signature(row) == receipt.applied_signature
    )
    if unchanged:
        desired = _bundle_values(data)
        if any(getattr(row, key) != value for key, value in desired.items()):
            for key, value in desired.items():
                setattr(row, key, value)
            row.fingerprint = hashlib.sha256(("None\n" + row.content).encode()).hexdigest()
            row.revision += 1
            row.updated_at = time.time()
            knowledge.reindex(db, row)
        receipt.applied_document_revision = row.revision
        receipt.applied_signature = _document_signature(row)
        receipt.applied_bundle_revision = package.revision
    receipt.seen_bundle_revision = package.revision
    receipt.updated_at = time.time()
    return False


def _retire_batch(db, package, saved, limit):
    cursor = saved.get("retirement_after", "")
    receipts = list(
        db.scalars(
            select(AIBundleDocument)
            .where(
                AIBundleDocument.source_ref > cursor,
                AIBundleDocument.seen_bundle_revision != package.revision,
            )
            .order_by(AIBundleDocument.source_ref)
            .limit(limit)
        )
    )
    for receipt in receipts:
        row = db.get(AIDocument, receipt.document_id)
        if (
            row is not None
            and row.revision == receipt.applied_document_revision
            and _document_signature(row) == receipt.applied_signature
            and row.state == "active"
        ):
            row.state = "candidate"
            row.revision += 1
            row.updated_at = time.time()
            knowledge.reindex(db, row)
            receipt.applied_document_revision = row.revision
            receipt.applied_signature = _document_signature(row)
            receipt.applied_bundle_revision = package.revision
            receipt.updated_at = time.time()
    if receipts:
        saved["retirement_after"] = receipts[-1].source_ref
    saved["state"] = "ready" if len(receipts) < limit else "retiring"


def step(db, settings, *, batch_size=25):
    if not policy.host_status(settings)["supported"]:
        return status(db, settings)
    # Avoid touching a potentially large bundle while disabled. This is only
    # a fast path; the authoritative check is repeated under ai-controls.
    if not policy.config(db)["enabled"]:
        return status(db, settings)
    package, error = inspect(settings)
    if error:
        value = _empty("failed", error)
        _save(db, value)
        db.commit()
        return value
    limit = max(1, min(batch_size, 100))
    with database_idempotency_lock(db, "ai-controls"):
        db.expire_all()
        if not policy.config(db)["enabled"]:
            return status(db, settings)
        with database_idempotency_lock(db, "ai-knowledge"):
            try:
                saved = _saved(db)
                if saved.get("revision") != package.revision:
                    saved = {
                        **_empty("importing"),
                        "revision": package.revision,
                        "total": len(package.offsets),
                        "retirement_after": "",
                    }
                start = saved["processed"]
                if type(start) is not int or not 0 <= start <= len(package.offsets):
                    raise ValueError("ai_bundle_progress_invalid")
                end = min(start + limit, len(package.offsets))
                for data in _read_batch(package, start, end):
                    created = _apply_record(db, package, data)
                    saved["created" if created else "skipped"] += 1
                saved.update(processed=end, state="importing")
                if end == len(package.offsets):
                    saved["state"] = "retiring"
                    _retire_batch(db, package, saved, limit)
                _save(db, saved)
                db.commit()
                return status(db, settings)
            except Exception:
                db.rollback()
                raise
