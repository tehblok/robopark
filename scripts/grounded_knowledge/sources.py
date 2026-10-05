"""Keep original message boundaries and hashes; do not infer answers or repairs."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

from .documents import extract_original

MAX_FILE = 128 * 1024**2


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_file(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE:
        raise ValueError("source_file_invalid")
    before = path.stat()
    data = path.read_bytes()
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ValueError("source_file_changed")
    return data


@contextmanager
def atomic_directory(output):
    """Build in a sibling directory and expose it only after success."""
    output = Path(output)
    if os.path.lexists(output):
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent)
    )
    staging = temporary_root / "payload"
    try:
        yield staging
        if not staging.is_dir():
            raise ValueError("staged_output_missing")
        if os.path.lexists(output):
            raise FileExistsError(output)
        os.rename(staging, output)
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)


def _open_original(root, relative):
    root = Path(root)
    relative = Path(relative)
    if (
        root.is_symlink()
        or not root.is_dir()
        or relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError("grounding_original_path_invalid")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(root, flags | directory | no_follow)
        for index, part in enumerate(relative.parts):
            child_flags = flags | no_follow
            if index < len(relative.parts) - 1:
                child_flags |= directory
            child = os.open(part, child_flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except OSError as error:
        if "descriptor" in locals():
            os.close(descriptor)
        raise ValueError("grounding_original_path_invalid") from error


def copy_verified_original(root, relative, target, expected_sha256):
    """Copy from a no-follow fd and validate the bytes written to staging."""
    target = Path(target)
    source_fd = _open_original(root, relative)
    target_fd = None
    try:
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE:
            raise ValueError("grounding_original_path_invalid")
        target_fd = os.open(
            target,
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                view = view[os.write(target_fd, view) :]
        os.fsync(target_fd)
        os.lseek(target_fd, 0, os.SEEK_SET)
        copied = hashlib.sha256()
        while True:
            chunk = os.read(target_fd, 1024 * 1024)
            if not chunk:
                break
            copied.update(chunk)
        after = os.fstat(source_fd)
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        if (
            before_identity != after_identity
            or copied.hexdigest() != expected_sha256
        ):
            raise ValueError("grounding_original_changed")
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        os.close(source_fd)
        if target_fd is not None:
            os.close(target_fd)


def flatten(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(flatten(item) for item in value)
    if isinstance(value, dict):
        return flatten(value.get("text", ""))
    return ""


def telegram_records(value):
    """Preserve short and unanswered messages; a reply link alone is not agreement."""
    chat_id = value["id"]
    messages = value["messages"]
    by_id = {message["id"]: message for message in messages}
    for index, message in enumerate(messages):
        if message.get("action") == "topic_created":
            yield {
                "source_ref": f"telegram:{chat_id}:topic:{message['id']}",
                "kind": "conversation_metadata",
                "locator": f"/messages/{index}/title",
                "text": str(message.get("title", "")),
                "date": message.get("date"),
            }
            continue
        if message.get("type") != "message":
            continue
        text = flatten(message.get("text"))
        parent = message.get("reply_to_message_id")
        topic = by_id.get(parent, {}).get("action") == "topic_created"
        yield {
            "source_ref": f"telegram:{chat_id}:message:{message['id']}",
            "kind": "conversation",
            "locator": f"/messages/{index}/text",
            "text": text,
            "date": message.get("date"),
            "reply_to": f"telegram:{chat_id}:message:{parent}"
            if parent is not None and not topic
            else None,
            "topic_ref": f"telegram:{chat_id}:topic:{parent}" if topic else None,
            "reply_missing": parent is not None and parent not in by_id,
            "attachment": message.get("file") or message.get("photo"),
        }


def tracker_records(value):
    key = value["key"]
    sections = value["sections"]
    issue = sections.get("issue", {})
    if not issue.get("ok") or not isinstance(issue.get("value"), dict):
        raise ValueError("source_issue_missing")
    issue = issue["value"]
    for field in ("summary", "description"):
        yield {
            "source_ref": f"tracker:{key}:{field}",
            "kind": "ticket",
            "locator": f"/sections/issue/value/{field}",
            "text": flatten(issue.get(field)),
            "date": issue.get("createdAt"),
            "issue_key": key,
        }
    comments = sections.get("comments", {})
    if not comments.get("ok") or not isinstance(comments.get("value"), list):
        raise ValueError("source_comments_missing")
    for index, comment in enumerate(comments["value"]):
        yield {
            "source_ref": f"tracker:{key}:comment:{comment['id']}",
            "kind": "ticket",
            "locator": f"/sections/comments/value/{index}/text",
            "text": flatten(comment.get("text")),
            "date": comment.get("createdAt"),
            "issue_key": key,
        }


def build(source, prepared, output):
    source, prepared, output = Path(source), Path(prepared), Path(output)
    if any(path.is_symlink() for path in (source, prepared, output)):
        raise ValueError("source_root_invalid")
    source, prepared, output = source.resolve(), prepared.resolve(), output.resolve()
    for root in (source, prepared):
        if output.is_relative_to(root) or root.is_relative_to(output):
            raise ValueError("source_output_overlaps_input")
    with atomic_directory(output) as staging:
        return _build(source, prepared, staging)


def _build(source, prepared, output):
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    counts, files, seen, issues, chat_files = Counter(), {}, {}, 0, 0
    errors = []
    issue_keys = set()
    seed_path = output / "sources.jsonl"
    fd = os.open(seed_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as target:

        def emit(row, path, file_sha):
            text_sha = digest(row["text"].encode())
            relative = path.relative_to(source).as_posix()
            files[relative] = file_sha
            # Equal text with different context is not equivalent evidence.
            identity = digest(
                json.dumps(row, sort_keys=True, ensure_ascii=False).encode()
            )
            if row["source_ref"] in seen:
                if seen[row["source_ref"]] != identity:
                    raise ValueError("source_duplicate_conflict")
                counts["identical_duplicate"] += 1
                return
            seen[row["source_ref"]] = identity
            record = dict(
                row, relative_path=relative, text_sha256=text_sha, file_sha256=file_sha
            )
            target.write(
                json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
            counts[row["kind"]] += 1
            if row.get("reply_missing"):
                counts["reply_missing"] += 1
            if row.get("attachment") and not row["text"].strip():
                counts["attachment_without_text"] += 1

        for path in sorted(source.glob("ChatExport*/result.json")):
            if not path.resolve().is_relative_to(source):
                raise ValueError("source_path_invalid")
            raw = read_file(path)
            value = json.loads(raw)
            chat_files += 1
            file_sha = digest(raw)
            for row in telegram_records(value):
                emit(row, path, file_sha)
        for name in ("config", "discovery", "manifest"):
            path = source / "tracker_year_all_parks" / f"{name}.json"
            if path.exists():
                raw = read_file(path)
                emit(
                    {
                        "source_ref": f"export:tracker:{name}",
                        "kind": "dataset",
                        "locator": "/",
                        "text": raw.decode("utf-8"),
                    },
                    path,
                    digest(raw),
                )
        for path in sorted((source / "tracker_year_all_parks/records").glob("*.json")):
            if not path.resolve().is_relative_to(source):
                raise ValueError("source_path_invalid")
            raw = read_file(path)
            try:
                value = json.loads(raw)
                rows = list(tracker_records(value))
                if value["key"] in issue_keys:
                    raise ValueError("source_issue_duplicate")
                issue_keys.add(value["key"])
                issues += 1
                file_sha = digest(raw)
                for row in rows:
                    emit(row, path, file_sha)
            except (ValueError, KeyError, TypeError) as error:
                errors.append(
                    {
                        "path": path.relative_to(source).as_posix(),
                        "error": type(error).__name__,
                    }
                )
        keys_file = source / "tracker_year_all_parks/keys.json"
        if keys_file.exists():
            expected = json.loads(read_file(keys_file))
            if (
                not isinstance(expected, list)
                or len(expected) != len(set(expected))
                or set(expected) != issue_keys
            ):
                raise ValueError("source_tracker_selection_incomplete")
        manifest_file = source / "tracker_year_all_parks/manifest.json"
        if manifest_file.exists():
            declared = (
                json.loads(read_file(manifest_file)).get("counts", {}).get("records")
            )
            if declared != issues:
                raise ValueError("source_tracker_selection_incomplete")
        mapping = json.loads(read_file(prepared / "source-map.private.json"))
        # The old index only selects paths; its prepared text is never evidence.
        if "sources" in mapping:
            mapping = mapping["sources"]
        relatives = {item.get("relative_path", "") for item in mapping.values()}
        for relative in sorted(relatives):
            suffix = Path(relative).suffix.lower()
            if suffix not in {".pdf", ".docx", ".json", ".xls", ".xlsx", ".xlsm"}:
                continue
            if Path(relative).name == "result.json":
                continue  # Telegram originals were captured without lossy filtering.
            path = source / relative
            if not path.resolve().is_relative_to(source):
                raise ValueError("source_path_invalid")
            file_sha = digest(read_file(path))
            rows = extract_original(source, relative)
            if digest(read_file(path)) != file_sha:
                raise ValueError("source_file_changed")
            for row in rows:
                emit(
                    {
                        "source_ref": row["source_ref"],
                        "kind": "document"
                        if suffix in {".pdf", ".docx"}
                        else "reference",
                        "locator": row["source_ref"].split("#", 1)[-1],
                        "text": row["content"],
                        "title": row["title"],
                        "extraction": "original_text_not_visual_validation",
                    },
                    path,
                    file_sha,
                )
    report = {
        "schema": 1,
        "records": len(seen),
        "source_files": len(files),
        "tracker_issues": issues,
        "chat_exports": chat_files,
        "counts": dict(counts),
        "errors": errors,
        "sourcebook_sha256": digest(seed_path.read_bytes()),
        "source_file_hashes": files,
        "not_a_knowledge_bundle": True,
    }
    (output / "manifest.private.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    (output / "manifest.private.json").chmod(0o600)
    if errors:
        raise ValueError("sourcebook_incomplete")
    return {key: value for key, value in report.items() if key != "source_file_hashes"}
